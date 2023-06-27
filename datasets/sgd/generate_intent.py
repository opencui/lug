#!/usr/bin/env python
# coding: utf-8


from cgi import print_directory
import json
from operator import truediv
import os
from pickle import NONE
from string import Template
import random
from collections import defaultdict
import faiss
import time
from absl import app
from absl import flags
from sklearn import pipeline
from tqdm import tqdm
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import sys
# import mkl
# mkl.get_max_threads()
import re
import string

model = SentenceTransformer('bert-base-nli-mean-tokens')

flags.DEFINE_bool(
    'fix_random_seed', True,
    'use fixed random seed(for debug)')

flags.DEFINE_string(
    'base_path', './dev/',
    'path to input file (original dataset)'
)
flags.DEFINE_string(
    'output', './res/dev/',
    'output path to save generated intent model examples'
)
flags.DEFINE_string(
    'stop_word_path', './stop_words.txt',
    'output path to save generated intent model examples'
)
flags.DEFINE_integer(
    'pos_num', -1,
    'num of positive sample num generated by each intent'
)
flags.DEFINE_integer(
    'neg_num', -1,
    'num of negative sample num generated by each intent'
)

flags.DEFINE_float(
    'negative_proportions', 1.0,
    'how many negative examples to generate for each positive example')

flags.DEFINE_float(
    'training_percentage', 1.0,
    'percentage for training')

flags.DEFINE_float(
    'dev_percentage', 0.0,
    'percentage for dev')

flags.DEFINE_string(
    'decode_method','transformer',
    'embedding method for the sentence'
)

flags.DEFINE_string(
    'cover_filter',"True",
    'whether we use the cover_relation to filter the sentence'
)

flags.DEFINE_string(
    'random_generate',"True",
    'generating the utterance by replacing the slot name with slot val instead of just using the original utterance'
)


FLAGS = flags.FLAGS

MODEL = "intents"
TRAIN = "train"
TEST = "test"
DEV = "dev"


def check_stop_words(slot_dict, utterance,string_list,stop_words_path):
    stop_words=[]
    with open(stop_words_path, encoding='utf-8') as f:
        items=f.readlines()
        for t in items:
            stop_words.append(t.lower()[:-1])
    stop_words=set(stop_words)
    # print("stop_words",stop_words)
    single_dict = dict()
    if string_list != []:
        for key, values in slot_dict.items():
            for value in values:
                single_dict[value] = key
        string_list=sorted(string_list,key=lambda x:x[0])
        res_utterance=utterance[:string_list[0][0]]
        for i,(cur_start,cur_end) in enumerate(string_list) :

            if i == len(string_list)-1 :
                res_utterance=res_utterance+utterance[cur_end:]
            else :
                res_utterance = res_utterance+utterance[cur_end:string_list[i+1][0]]

    else:
        res_utterance=utterance
    punctuation_string = string.punctuation
    for i in punctuation_string:
        res_utterance = res_utterance.replace(i, '')

    all_not_slot_words=set(res_utterance.split())

    if len(all_not_slot_words-stop_words) >=2:
        return True
    return False

class IntentTemplate:
    """
    restore the all template of a certain intents, including the set of all possible examplers ,and the dict for all slot 
    """
    def __init__(self):
        self.exampler_set=set()
        self.slot_dict=defaultdict(set)
    
    def generate_expression_template(self,slot_dict, utterance,string_list):
        '''
        replacing the slot val with the slot name,to avoid match the short slot val which may be inclued in other long slot val,we need sort by the length of the slot val
        '''
        if string_list == []:
            return utterance
        single_dict = dict()

        for key, values in slot_dict.items():
            for value in values:
                single_dict[value] = key


        string_list=sorted(string_list,key=lambda x:x[0])
        res_utterance=utterance[:string_list[0][0]]
        for i,(cur_start,cur_end) in enumerate(string_list) :
            # if len(string_list) >=2:
            #     print("sub string",utterance[cur_start:cur_end])
            res_utterance = res_utterance+' < '+single_dict[utterance[cur_start:cur_end]]+' > '
            if i == len(string_list)-1 :
                res_utterance=res_utterance+utterance[cur_end:]
            else :
                res_utterance = res_utterance+utterance[cur_end:string_list[i+1][0]]

        return res_utterance

    def add_sample(self,expression):
        expression_template=self.generate_expression_template(expression.slots,expression.utterance,expression.string_list)

        self.exampler_set.add(expression_template)
        no_underscore_utterance=expression_template
        for key, values in expression.slots.items():
            no_underscore_utterance=no_underscore_utterance.replace(key,' '.join(key.split('_')))
        expression.exampler=no_underscore_utterance

        for slot_name,slot_val_list  in expression.slots.items():
            for slot_val in slot_val_list:
                self.slot_dict[slot_name].add(slot_val)
        
    def generate_equal_sample(self,expression):
        expression_template=self.generate_expression_template(expression.slots,expression.utterance,expression.string_list)
        for slot_name,slot_vals  in self.slot_dict.items():
            if '< '+slot_name+' >'   in  expression_template:
                expression_template=expression_template.replace('< '+slot_name+' >',list(slot_vals)[random.randint(0,len(slot_vals)-1)])
        return expression_template
    def show(self):
        print(self.exampler_set)
        print(self.slot_dict)
        

class Expression:
    """
    expression examples
    """

    def __init__(self, expression, intent, slots,string_list=None):
        self.utterance = expression
        self.intent = intent
        self.slots = slots  # dict to store slot, value pairs
        self.idx = None
        self.string_list=string_list
        self.exampler=None


def cover_realtion(expression_A,expression_B):
    '''
    check if the slot of A could cover all of slot of B
    '''
    return set(expression_B.slots.keys()).issubset(set(expression_A.slots.keys())) 

def slot_val_To_slot_name(slot_dict, utterance):
    '''
    replacing the slot val with the slot name,to avoid match the short slot val which may be inclued in other long slot val,we need sort by the length of the slot val
    '''
    single_dict = dict()

    for key, values in slot_dict.items():
        for value in values:
            single_dict[value] = key

    single_dict = sorted(single_dict.items(), key=lambda x: len(x[0]), reverse=True)

    for (value, key) in single_dict:
        utterance = utterance.replace(value, '< ' + ' '.join(key.split('_')) + ' >')

    return utterance

def load(base_path):
    """
    load original sgd data and create expression examples
    :param path: input path to original sgd dataset
    :return: expression examples
    """
    intent_expressions = defaultdict(list)
    intent_template_dict=defaultdict(IntentTemplate)
    files = os.listdir(base_path)
    sentence_set=defaultdict(set)
    for file in files:
        if file[:6] == 'dialog':
            with open(base_path + file, encoding='utf-8') as f:
                f = json.load(f)
                for dialogue in f:
                    for key, value in dialogue.items():
                        if key == 'turns':
                            preintent_all = set()
                            for idx,turn in enumerate(value):
                                if turn['speaker'] == 'USER':
                                    intent_all = set()
                                    for frame in turn['frames']:
                                        intent_all.add(frame['state']['active_intent'])
                                    if idx-1>=0 and value[idx-1]["frames"][0]["actions"][0]["act"]=="OFFER_INTENT" :
                                        check_intent=set(value[idx-1]["frames"][0]["actions"][0]["values"])
                                    else :
                                        check_intent=set()

                                    if intent_all-preintent_all or (not preintent_all):
                                        for frame in turn['frames']:
                                            if  frame['service'][-1] == '1'  and  (frame['state']['active_intent'] in  intent_all-preintent_all)  and frame['state']['active_intent']  != 'NONE' and frame['state']['active_intent'] not in check_intent:
                                                string_list=[]
                                                utterance_slot = defaultdict(list)
                                                for _slot in frame['slots']:
                                                    utterance_slot[_slot['slot']].append(turn['utterance'][_slot['start']:_slot['exclusive_end']].lower())
                                                    string_list.append((_slot['start'],_slot['exclusive_end']))

                                                if check_stop_words(utterance_slot,turn['utterance'].lower(),string_list,FLAGS.stop_word_path) ==False:
                                                    continue
                                                expression = Expression(turn['utterance'].lower(), frame['state']['active_intent'] ,utterance_slot,string_list)
                                                intent_template_dict[frame['state']['active_intent']].add_sample(expression)
                                                intent_expressions[frame['state']['active_intent']].append(expression)
                                                sentence_set[frame['state']['active_intent']].add(expression.utterance)
                                    preintent_all = intent_all
    return intent_expressions,intent_template_dict


class SearchSimilarExpressions:
    """
    using sentence-transformer to encode all the utternace with new intent
    """

    def __init__(self, intent_expressions):
        self.expression_corpus = []  # expression corpus used to be encoded by bert for all expressions
        self.idx2expression = {}  # map idx to expression object
        self.intent_range = {}
        self.sentence_embeddings =None
        self.tfidf_matrix = None
        idx = 0
        stt = 0
        for intent, expressions in intent_expressions.items():
            end = len(expressions)
            self.intent_range[intent] = (stt,stt + end)  # give the range of the expressions in the expresssion_corpus for every intent,  left closed right open
            stt += end
            for expression in expressions:
                self.expression_corpus.append(expression.utterance)

                expression.idx = idx
                self.idx2expression[idx] = expression  # given the index for the order of the expresssion,idx indicates the order of the sentence in the toaal expressionss
                idx += 1

        idf_vectorizer = TfidfVectorizer(use_idf=True)
        self.tfidf_matrix = idf_vectorizer.fit_transform(self.expression_corpus).toarray()
        self.sentence_embeddings = model.encode(self.expression_corpus)




def dataset_type(train_percentage, dev_percentage):
    val = random.random()
    if val < train_percentage:
        return TRAIN
    elif val < (train_percentage + dev_percentage):
        return DEV
    return TEST

class IntentExample:
    def __init__(self, quadruple):
        self.type = "intent"
        self.source = quadruple[0]
        self.label = quadruple[1]
        self.utterance = quadruple[2]
        self.exemplar = quadruple[3]


class IntentExampleGenerator:
    """
    generate examples
    """

    def __init__(self, training_percentage, neg_percentage, intent_template_dict,seed=None):
        if training_percentage < 0.0 or training_percentage > 1.0:
            raise ValueError("training_percentage is out of range")
        self.neg_percentage = neg_percentage
        self.training_percentage = training_percentage
        self.seed = seed
        self.intent_template_dict=intent_template_dict

    def __call__(self, expressions):
        examples = defaultdict(list)
        random.seed(self.seed)
        starttime = time.time()
        SSE = SearchSimilarExpressions(expressions)
        expression_corpus = SSE.expression_corpus
        intent_range = SSE.intent_range
        idx2expression = SSE.idx2expression

        if FLAGS.decode_method == 'tfidf':
            embed_matrix=SSE.tfidf_matrix.astype('float32')
        elif FLAGS.decode_method == 'transformer':
            embed_matrix = SSE.sentence_embeddings.astype('float32')
        embed_dim = embed_matrix.shape[1]
        dim, measure = embed_dim, faiss.METRIC_INNER_PRODUCT
        param = 'Flat' 
        index = faiss.index_factory(dim, param, measure)

        index.add(embed_matrix)

        
        total_positive_cnt=0
        total_negative_cnt=0
        for intent, range_ in tqdm(intent_range.items()):
            intent_sample = []
            entire_positive_sample = []

            for i in tqdm(range(range_[0], range_[1])):  
                for j in range(range_[0], range_[1]):
                    # if i != j:#is add equal sent,we need to delete it
                    if FLAGS.cover_filter == "True":
                        if cover_realtion(idx2expression[i],idx2expression[j]):
                            if FLAGS.random_generate == 'True': 
                                equal_sent=self.intent_template_dict[idx2expression[i].intent].generate_equal_sample(idx2expression[i])
                                pair = [intent, "1", equal_sent,idx2expression[j].exampler]

                            else:
                                pair = [intent, "1", expression_corpus[i],idx2expression[j].exampler]

                            entire_positive_sample.append(json.dumps(IntentExample(pair)))
                    else:
                        if FLAGS.random_generate == 'True': 
                            equal_sent=self.intent_template_dict[idx2expression[i].intent].generate_equal_sample(idx2expression[i])
                            pair = [intent, "1", equal_sent,idx2expression[j].exampler]

                        else:
                            pair = [intent, "1", expression_corpus[i],idx2expression[j].exampler]

                            
                        entire_positive_sample.append(json.dumps(IntentExample(pair)))
            entire_positive_sample=list(set(entire_positive_sample))

            if (len(entire_positive_sample) < FLAGS.pos_num or FLAGS.pos_num == -1):
                subsample_pos = entire_positive_sample
            else:
                subsample_pos = random.sample(entire_positive_sample, FLAGS.pos_num)
            positive_cnt = len(subsample_pos)
            for item in subsample_pos:
                partition = dataset_type(FLAGS.training_percentage, FLAGS.dev_percentage)
                examples[partition].append(item)
                intent_sample.append(item)


            entire_negative_sample = []
            if FLAGS.cover_filter == "True":
                k= int((range_[1]-range_[0])*1.3)
            else:
                k= int((range_[1]-range_[0])*1.7)
            D, I = index.search(embed_matrix[range_[0]:range_[1]], k) 
            for i in tqdm(range(range_[0], range_[1])):
                topkidx = I[i - range_[0]]
                similar_neg_ex_idx = topkidx[((topkidx  < range_[0])  | (topkidx >= range_[1])) &  (topkidx != -1)]  
                for idx in similar_neg_ex_idx:
                    neg_expression = idx2expression[idx]
                    neg_expression_utterance = neg_expression.utterance
                    neg_expression_intent = neg_expression.intent
                    if FLAGS.random_generate == 'True':
                        equal_sent=self.intent_template_dict[idx2expression[i].intent].generate_equal_sample(idx2expression[i])
                        pair = [intent + "_" + neg_expression_intent, "0", equal_sent, neg_expression.exampler]
                    else:
                        pair = [intent + "_" + neg_expression_intent, "0", expression_corpus[i],neg_expression.exampler]
                    entire_negative_sample.append(json.dumps(IntentExample(pair)))

            entire_negative_sample=list(set(entire_negative_sample))
            if (len(entire_negative_sample) < FLAGS.neg_num  or  FLAGS.neg_num == -1):
                subsample_neg = entire_negative_sample
            else:
                subsample_neg = random.sample(entire_negative_sample, FLAGS.neg_num)
            negative_cnt = len(subsample_neg)
            for item in subsample_neg:
                partition = dataset_type(FLAGS.training_percentage, FLAGS.dev_percentage)
                examples[partition].append(item)
                intent_sample.append(item)

            print(intent, "(pos:", positive_cnt, "neg:", negative_cnt, ")")
            total_positive_cnt+=positive_cnt
            total_negative_cnt+=negative_cnt
        print(f'total_positive_cnt :{total_positive_cnt} total_negative_cnt :{total_negative_cnt}  total:{total_positive_cnt+total_negative_cnt} ')
        endtime = time.time()
        print("total Time ", (endtime - starttime))
        return examples


def save(intent_examples, path):
    """
    save generated examples into tsv files
    :param intent_examples:  generated examples
    :param path: output path
    :return: None
    """
    for key, examples in intent_examples.items():
        # we only generate one file for each folder
        with open(os.path.join(path, MODEL +'_'+FLAGS.decode_method+'_random_generate_'+FLAGS.random_generate+'_cover_filter_'+FLAGS.cover_filter+"pos_num"+str(FLAGS.pos_num)+"neg_num"+str(FLAGS.neg_num)+"."+FLAGS.base_path[2:-1])+'cleaned', 'w', encoding='utf-8') as f:
            for example in examples:
                f.write(example + "\n")        
    return

def main(_):
    intent_expressions,intent_template_dict = load(FLAGS.base_path)
    build_intent_examples = IntentExampleGenerator(FLAGS.training_percentage, FLAGS.negative_proportions,intent_template_dict)
    if FLAGS.fix_random_seed:
        build_intent_examples.seed = 202006171752
    intent_examples = build_intent_examples(intent_expressions)
    save(intent_examples, FLAGS.output)


if __name__ == '__main__':
    app.run(main)