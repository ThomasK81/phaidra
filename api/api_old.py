from __future__ import unicode_literals
# coding: utf8
from django.core.management import setup_environ
from phaidra import settings
from tastypie.bundle import Bundle
from django.contrib.webdesign.lorem_ipsum import sentence
setup_environ(settings)
from django.conf.urls import url
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.middleware.csrf import _get_new_csrf_key as get_new_csrf_key
from django.middleware.csrf import _sanitize_token, constant_time_compare

from app.models import Slide, Submission, AppUser, Document, Sentence, Word, Lemma

from neo4jrestclient.client import GraphDatabase
from neo4jrestclient import client

from tastypie import fields
from tastypie.authentication import BasicAuthentication, SessionAuthentication, MultiAuthentication, Authentication
from tastypie.authorization import Authorization, ReadOnlyAuthorization
from tastypie.exceptions import Unauthorized
from tastypie.resources import ModelResource, ALL, ALL_WITH_RELATIONS
from tastypie.utils import trailing_slash
from tastypie.http import HttpUnauthorized, HttpForbidden, HttpBadRequest

import os
import json
import random
from random import shuffle
from neo4django.db.models import NodeModel

import time

class UserObjectsOnlyAuthorization(Authorization):
	def read_list(self, object_list, bundle):
		user_pk = bundle.request.user.pk
		return object_list.filter(user=user_pk)
	
	def read_detail(self, object_list, bundle):
		return bundle.obj.user == bundle.request.user
	
	def create_list(self, object_list, bundle):
		return object_list

	def create_detail(self, object_list, bundle):
		return bundle.obj.user == bundle.request.user

	def update_list(self, object_list, bundle):
		allowed = []

		for obj in object_list:
			if obj.user == bundle.request.user:
				allowed.append(obj)

		return allowed
	
	def update_detail(self, object_list, bundle):
		return bundle.obj.user == bundle.request.user.pk
	
	def delete_list(self, object_list, bundle):
		raise Unauthorized("Deletion forbidden")
	
	def delete_detail(self, object_list, bundle):
		raise Unauthorized("Deletion forbidden")

class UserResource(ModelResource):
	class Meta:
		queryset = AppUser.objects.all()
		resource_name = 'user'
		fields = ['first_name', 'last_name', 'username', 'email', 'is_staff']
		allowed_methods = ['get', 'post', 'patch']
		always_return_data = True
		authentication = SessionAuthentication()
		authorization = Authorization()

	def prepend_urls(self):
		params = (self._meta.resource_name, trailing_slash())
		return [
			url(r"^(?P<resource_name>%s)/login%s$" % params, self.wrap_view('login'), name="api_login"),
			url(r"^(?P<resource_name>%s)/logout%s$" % params, self.wrap_view('logout'), name="api_logout")
		]

	def login(self, request, **kwargs):
		"""
		Authenticate a user, create a CSRF token for them, and return the user object as JSON.
		"""
		self.method_check(request, allowed=['post'])
		
		data = self.deserialize(request, request.raw_post_data, format=request.META.get('CONTENT_TYPE', 'application/json'))

		username = data.get('username', '')
		password = data.get('password', '')

		if username == '' or password == '':
			return self.create_response(request, {
				'success': False,
				'error_message': 'Missing username or password'
			})
		
		user = authenticate(username=username, password=password)
		
		if user:
			if user.is_active:
				login(request, user)
				response = self.create_response(request, {
					'success': True,
					'username': user.username
				})
				response.set_cookie("csrftoken", get_new_csrf_key())
				return response
			else:
				return self.create_response(request, {
					'success': False,
					'reason': 'disabled',
				}, HttpForbidden)
		else:
			return self.create_response(request, {
				'success': False,
				'error_message': 'Incorrect username or password'
			})
			
	def logout(self, request, **kwargs):
		""" 
		Attempt to log a user out, and return success status.		
		"""
		self.method_check(request, allowed=['get'])
		self.is_authenticated(request)
		if request.user and request.user.is_authenticated():
			logout(request)
			return self.create_response(request, { 'success': True })
		else:
			return self.create_response(request, { 'success': False, 'error_message': 'You are not authenticated, %s' % request.user.is_authenticated() })

	def post_list(self, request, **kwargs):
		"""
		Make sure the user isn't already registered, create the user, return user object as JSON.
		"""
		self.method_check(request, allowed=['post'])
		data = self.deserialize(request, request.raw_post_data, format=request.META.get('CONTENT_TYPE', 'application/json'))

		try:
			user = AppUser.objects.create_user(
				data.get("username"),
				data.get("email"),
				data.get("password")
			)
			user.save()
		except IntegrityError as e:
			return self.create_response(request, {
				'success': False,
				'error': e
			})

		return self.create_response(request, {
			'success': True
		})


	def read_list(self, object_list, bundle):
		"""
		Allow the endpoint for the User Resource to display only the logged in user's information
		"""
		self.is_authenticated(request)
		return object_list.filter(pk=bundle.request.user.id)

	def patch_detail(self, request, **kwargs):
		"""
		Update the fields of a user and return the updated User Resource.	
		"""
		try:
			node = AppUser.objects.select_related(depth=1).get(id=kwargs["pk"])
		except ObjectDoesNotExist:
			raise Http404("Cannot find user.")

		body = json.loads(request.body) if type(request.body) is str else request.body
		data = body.copy()

		restricted_fields = ['is_staff', 'is_user', 'username', 'password']

		for field in body:
			if hasattr(node, field) and not field.startswith("_"):
				attr = getattr(node, field)
				value = data[field]

				# Do not alter relationship fields from this endpoint
				if not hasattr(attr, "_rel") and field not in restricted_fields:
					setattr(node, field, value)
				else:
					return self.create_response(request, {
						'success': False,
						'error_message': 'You are not authorized to update this field.'
					})
				continue

			# This field is not contained in our model, so discard it
			del data[field]

		if len(data) > 0:
			node.save()

		# Returns all field data of the related user as response data
		data = {}		
		for property_name in node.property_names(): 		
			data[property_name] = getattr(node, property_name)

		return self.create_response(request, data)


class SlideResource(ModelResource):
	class Meta:
		allowed_methods = ['post', 'get', 'patch']
		always_return_data = True
		authentication = SessionAuthentication()
		#authentication = BasicAuthentication()
		authorization = Authorization()
		excludes = ['answers', 'require_order', 'require_all_answers']
		queryset = Slide.objects.all()
		resource_name = 'slide'

class SubmissionResource(ModelResource):
	class Meta:
		allowed_methods = ['post', 'get', 'patch']
		always_return_data = True
		authentication = SessionAuthentication() 
		#authentication = BasicAuthentication()
		authorization = UserObjectsOnlyAuthorization()
		excludes = ['require_order', 'require_all']
		queryset = Submission.objects.all()
		resource_name = 'submission'

	# This cannot be the best way of doing this, but deadlines are looming. 
	# For a cleaner implementation, see: https://github.com/jplusplus/detective.io/blob/master/app/detective/individual.py
		
	def post_list(self, request, **kwargs):
		"""
		Create a new submission object, which relates to the slide it responds to and the user who submitted it.
		Return the submission object, complete with whether or not they got the answer correct.
		"""
		self.method_check(request, allowed=['post'])
		self.is_authenticated(request)

		if not request.user or not request.user.is_authenticated():
			return self.create_response(request, { 'success': False, 'error_message': 'You are not authenticated, %s' % request.user })

		data = self.deserialize(request, request.raw_post_data, format=request.META.get('CONTENT_TYPE', 'application/json'))

		# Get the slide node that the user is answering before creation of the submission -- this also needs further checks (e.g. can they even submit to this slide yet?)
		#try:
		#	slide_node = Slide.objects.get(pk=data.get("slide"))
		#except ObjectDoesNotExist as e:
		#	return self.create_response(request, {
		#		'success': False,
		#		'error': e
		#	})

		# Ensuring that the user is who s/he says s/he is, handled by user objs. auth.
		try:
			user_node = AppUser.objects.get(username=data.get("user"))
		except ObjectDoesNotExist as e:
			# Is it possible this could occur if the user passes authentication?
			return self.create_response(request, {
				'success': False,
				'error': e
			})

		# some validation in the manager class of the model
		# on errors which are not caught in the manager class, the node will still be created because save is called (too?) soon
		# look into django.db.models.Model save method for saving behaviour on error?!
		node = Submission.objects.create(
			response = data.get("response"), # string 
			task = data.get("task"), # string 
			smyth = data.get("smyth"),	# string
			time = int(data.get("time")),		 # integer
			accuracy = int(data.get("accuracy")), # integer
			encounteredWords = data.get("encounteredWords"), # array
			slideType = data.get("slideType"), # string
			timestamp = data.get("timestamp") # datetime
		)
		if node is None :
			# in case an error wasn't already raised 			
			raise ValidationError('Submission node could not be created.')
	
		# Form the connections from the new Submission node to the existing slide and user nodes
		#node.slide = slide_node
		node.user = user_node

		# create the body
		body = json.loads(request.body) if type(request.body) is str else request.body
		# data = body.clone()

		# Check to see if the user answered the question correctly or not
		node.save()

		return self.create_response(request, body)


class VisualizationResource(ModelResource):
							
	class Meta:
		#queryset = Word.objects.all()
		resource_name = 'visualization'
		#always_return_data = True
		#excludes = ['require_order', 'require_all']
		authorization = ReadOnlyAuthorization()

	def prepend_urls(self, *args, **kwargs):	
		
		return [
			url(r"^(?P<resource_name>%s)/%s%s$" % (self._meta.resource_name, 'encountered', trailing_slash()), self.wrap_view('encountered'), name="api_%s" % 'encountered')
			]

	#/api/visualization/encountered/?format=json&level=word-level&range=urn:cts:greekLit:tlg0003.tlg001.perseus-grc1:1.090.4:11-19&user=john
	def encountered(self, request, **kwargs):
		
		"""
		Start visualization...
		"""
		#fo = open("foo.txt", "wb")
		#millis = int(round(time.time() * 1000))
		#fo.write("%s start encountered method, get user: \n" % millis)
		
		gdb = GraphDatabase("http://localhost:7474/db/data/")
		level = request.GET.get('level')
		#user = AppUser.objects.get(username = request.GET.get('user'))
		submissions = gdb.query("""START n=node(*) MATCH (s)-[:answered_by]->(n) WHERE HAS (n.username) AND n.username =  '""" + request.GET.get('user') + """' RETURN s""")	
		data = {}	
		
		if level == "word-level":
			
			seenDict = {}
			knownDict = {}
			data['words'] = []
			
			#millis = int(round(time.time() * 1000))
			#fo.write("%s calculating cts ranges: \n" % millis)
			
			# calculate CTSs of the word range (later look them up within submissions of the user)
			wordRangeArray = []
			cts = request.GET.get('range')
			# get the stem
			endIndex = len(cts)-len(cts.split(':')[len(cts.split(':'))-1])
			rangeStem = cts[0:endIndex]		
			# get the numbers of the range and save the CTSs
			numbersArray = cts.split(':')[len(cts.split(':'))-1].split('-')
			for num in range(int(numbersArray[0]), int(numbersArray[1])+1):
				wordRangeArray.append(rangeStem + str(num))
			
			#millis = int(round(time.time() * 1000))
			#fo.write("%s reading smyth doc: \n" % millis)
			
			# get the file entry:
			filename = os.path.join(os.path.dirname(__file__), '../static/js/smyth.json')
			fileContent = {}
			with open(filename, 'r') as json_data:
				fileContent = json.load(json_data)
				json_data.close()
						
			#millis = int(round(time.time() * 1000))
			#fo.write("%s start running over range: \n" % millis)		
			
			for wordRef in wordRangeArray:
				
				#millis = int(round(time.time() * 1000))
				#fo.write("%s get the word obejct: \n" % millis)
				
				w = gdb.query("""START n=node(*) MATCH (n) WHERE HAS (n.CTS) AND n.CTS = '""" +wordRef+ """' RETURN n""")
				
				#table.data[0][0]['data']['CTS']
				#w = Word.objects.get(CTS = wordRef)
				seenDict[wordRef] = 0
				knownDict[wordRef] = False
				
				for sub in submissions.elements:	
				
					# get the morph info to the words via a file lookup of submission's smyth key, save params to test it on the encountered word of a submission
					params = {}
					grammarParams = fileContent[0][sub[0]['data']['smyth']]['query'].split('&')
					for pair in grammarParams:
						params[pair.split('=')[0]] = pair.split('=')[1]
														
					# get the encountered word's CTSs of this submission
					if wordRef in sub[0]['data']['encounteredWords']:			
												
						# if word learnt already known don't apply filter again
						if not knownDict[wordRef]:
							
							#millis = int(round(time.time() * 1000))
							#fo.write("%s range word in enc., check morph hits and incr. seen: \n" % millis)	
							
							# loop over params to get morph known infos							
							badmatch = False
							#for p in params.keys():
							#	if params[p] != getattr(w, p):
							#		badmatch = True
							#if not badmatch:
							#	knownDict[wordRef] = True	
							
							for p in params.keys():
								try:
									w.elements[0][0]['data'][p]
									if params[p] != w.elements[0][0].data[p]:
										badmatch = True
								except:
									badmatch = True
									
							if not badmatch:
								knownDict[wordRef] = True				
										
						# if word in requested range and in encountered save times seen
						try:
							seenDict[wordRef]
							seenDict[wordRef] = seenDict[wordRef] + 1
						except:
							seenDict[wordRef] = 1
							
				# save data
				if seenDict[wordRef] > 0:
					data['words'].append({'value': w.elements[0][0]['data']['value'], 'timesSeen' : seenDict[wordRef], 'morphKnown': knownDict[wordRef], 'synKnown': False, 'vocKnown': True, 'CTS': w.elements[0][0]['data']['CTS']})
					#data['words'].append({'value': w.value, 'timesSeen' : seenDict[wordRef], 'morphKnown': knownDict[wordRef], 'synKnown': False, 'vocKnown': True, 'CTS': w.CTS})
				else:
					data['words'].append({'value': w.elements[0][0]['data']['value'], 'timesSeen' : seenDict[wordRef], 'morphKnown': knownDict[wordRef], 'synKnown': False, 'vocKnown': False, 'CTS': w.elements[0][0]['data']['CTS']})
					#data['words'].append({'value': w.value, 'timesSeen' : seenDict[wordRef], 'morphKnown': knownDict[wordRef], 'synKnown': False, 'vocKnown': False, 'CTS': w.CTS})
		
			#millis = int(round(time.time() * 1000))
			#fo.write("%s all done: \n" % millis)
			#fo.close()
			
			return self.create_response(request, data)
		
		return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)	



class DocumentResource(ModelResource):
	
	#document/?format=json&sentences__length=24
	sentences = fields.ToManyField('api.api.SentenceResource', lambda bundle: Sentence.objects.filter(document__name = bundle.obj.name), null = True, blank = True )# full = True)
							
	class Meta:
		queryset = Document.objects.all()
		resource_name = 'document'
		always_return_data = True
		excludes = ['require_order', 'require_all']
		authorization = ReadOnlyAuthorization()
		filtering = {'internal': ALL,
					'CTS': ALL,
					'author': ALL,
					'name': ALL,
					'name_eng': ALL,
					'lang': ALL,
					'sentences': ALL_WITH_RELATIONS}


class SentenceResource(ModelResource):
	#sentence/?format=json&file__lang=fas
	file = fields.ForeignKey(DocumentResource, 'document')
	# expensive
	words = fields.ToManyField('api.api.WordResource', lambda bundle: Word.objects.filter(sentence__sentence=bundle.obj.sentence), null=True, blank=True, full = True)
		
	class Meta:
		queryset = Sentence.objects.all()
		resource_name = 'sentence'
		always_return_data = True
		excludes = ['require_order', 'require_all']
		authorization = ReadOnlyAuthorization()
		filtering = {'internal': ALL,
					'CTS': ALL,
					'length': ALL,
					'file': ALL_WITH_RELATIONS,
					'words': ALL_WITH_RELATIONS}
		limit = 5	
		
	"""
	Gets one or more short/long randomized/not random sentence(s) with provided morphological information to one word.
	Makes sure the query params are still supported by the short sentence.
	"""
	def get_list(self, request, **kwargs):
		
		query_params = {}
		for obj in request.GET.keys():
			if obj in dir(Word) and request.GET.get(obj) is not None:
				query_params[obj] = request.GET.get(obj)
			elif obj.split('__')[0] in dir(Word) and request.GET.get(obj) is not None:
				query_params[obj] = request.GET.get(obj)
		
		# if ordinary filter behavior is required, put key default
		if 'default' in request.GET.keys():		
			return super(SentenceResource, self).get_list(request, **kwargs)
		
		# filter on parameters 						
		words = Word.objects.filter(**query_params)
		
		if len(words) < 1:
			return self.error_response(request, {'error': 'No sentences hit your query.'}, response_class=HttpBadRequest)	
		
		data = {}
		data['sentences'] = []
		
		#/api/sentence/?randomized=&short=&format=json&lemma=κρατέω
		if 'randomized' in request.GET.keys():
						
			if 'short' in request.GET.keys():
				
				# make this hack only for randomized/short for performance improvement; run over sentences instead of words
				if len(query_params) < 1:
					
					x = list(Sentence.objects.all())
					sentences = sorted(x, key=lambda *args: random.random())
				
					for sentence in sentences:
						sentence = sentence.get_shortened(query_params)
						# asap check if the short sentence to a word's sentence returns a set with query params matching words
						if sentence is not None:
							
							tmp = {}
							tmp['words'] = []
							for word in sentence:
								w = word.__dict__
								tmp['words'].append(w['_prop_values'])
								
							data['sentences'].append(tmp)												
							return self.create_response(request, data)
						
					return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)
					
				else:
					
					x = list(words)
					words = sorted(x, key=lambda *args: random.random())
		
					for word in words:
						sentence = word.sentence
						sentence = sentence.get_shortened(query_params)
						# asap check if the short sentence to a word's sentence returns a set with query params matching words
						if sentence is not None:
							
							tmp = {}
							tmp['words'] = []
							for word in sentence:
								w = word.__dict__
								tmp['words'].append(w['_prop_values'])
								
							data['sentences'].append(tmp)														
							return self.create_response(request, data)
						
					return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)
			
			# randomized, long
			#/api/sentence/?randomized=&format=json&lemma=κρατέω	
			else:
				
				word = random.choice(words)
				sentence = word.sentence
				
				tmp = {'sentence': sentence.__dict__}
				tmp = tmp['sentence']['_prop_values']
				tmp['words'] = []	
				for word in reversed(sentence.words.all()):			
					w = word.__dict__
					tmp['words'].append(w['_prop_values'])
				
				data['sentences'].append(tmp)
				return self.create_response(request, data)
		
		# not randomized
		else:
			# not randomized, short and CTS queries a sentence via CTS
			if 'short' in request.GET.keys():
				
				CTS = request.GET.get('CTS')
				# if CTS is missed all sentences containing words that hit the query are returned, Expensive!!!
				#/api/sentence/?format=json&short=&form=ἀπέβησαν
				# make it a set	
				if CTS is None:
					
					for word in words:
						sentence = word.sentence
						# asap check if the short sentence to a word's sentence returns a set with query params matching words
						sentence = sentence.get_shortened(query_params)
					
						if sentence is not None:
							tmp = {}
							tmp['words'] = []	
							for word in sentence:
								w = word.__dict__
								tmp['words'].append(w['_prop_values'])
							
							data['sentences'].append(tmp)
							
					return self.create_response(request, data)
				
				# not randomized, short with CTS
				#/api/sentence/?format=json&short=&CTS=urn:cts:greekLit:tlg0003.tlg001.perseus-grc1:1.108.5
				# TODO: object sentence?????
				else:
					sentence = Sentence.objects.get(CTS = CTS)
					sentence = sentence.get_shortened({})
					
					tmp = {}
					tmp['words'] = []				
					for word in sentence:
						w = word.__dict__
						tmp['words'].append(w['_prop_values'])
						
					data['sentences'].append(tmp)					
					return self.create_response(request, data)
				
				return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)
			
			# not randomized, long, no CTS implies more than one sentence
			else:
				CTS = request.GET.get('CTS')
				# if CTS is missed all sentences containing words that hit the query are returned
				#/api/sentence/?format=json&form=ἀπέβησαν&lemma__endswith=νω
				if CTS is None:
					
					# and no other params -> default
					if len(query_params) < 1:	
						return super(SentenceResource, self).get_list(request, **kwargs)
				
					else:

						for word in words:
							
							sentence = word.sentence
							tmp = {'sentence': sentence.__dict__}
							tmp = tmp['sentence']['_prop_values']
							tmp['words'] = []	
							for word in reversed(sentence.words.all()):
						
								w = word.__dict__
								tmp['words'].append(w['_prop_values'])
								
							data['sentences'].append(tmp)
							
						return self.create_response(request, data)
				
				# not randomized, long, CTS return one sentence
				#/api/sentence/?format=json&CTS=urn:cts:greekLit:tlg0003.tlg001.perseus-grc1:1.108.5
				else:
						
					sentence = Sentence.objects.get(CTS = CTS)
								
					tmp = {'sentence': sentence.__dict__}
					tmp = tmp['sentence']['_prop_values']
					tmp['words'] = []
					for word in reversed(sentence.words.all()):
						
						w = word.__dict__
						tmp['words'].append(w['_prop_values'])
					
					data['sentences'].append(tmp)
					
					return self.create_response(request, data)
				
				return self.error_response(request, {'error': 'No sentences hit your query.'}, response_class=HttpBadRequest)
			
	
	
	def prepend_urls(self, *args, **kwargs):	
		
		return [
			url(r"^(?P<resource_name>%s)/%s%s$" % (self._meta.resource_name, 'get_one_random', trailing_slash()), self.wrap_view('get_one_random'), name="api_%s" % 'get_one_random'),
			url(r"^(?P<resource_name>%s)/%s%s$" % (self._meta.resource_name, 'get_one_random_short', trailing_slash()), self.wrap_view('get_one_random_short'), name="api_%s" % 'get_one_random_short')
			]
	
	#/api/sentence/get_one_random/?format=json&case=gen&lemma=Λακεδαιμόνιος
	def get_one_random(self, request, **kwargs):
		
		"""
		Gets one random sentence of sentences with provided morphological information to one word.
		"""
		length = request.GET.get('length')
		query_params = {}
		for obj in request.GET.keys():
			if obj in dir(Word) and request.GET.get(obj) is not None:
				query_params[obj] = request.GET.get(obj)
							
		words = Word.objects.filter(**query_params)
		if len(words) < 1:
			return self.error_response(request, {'error': 'No results hit this query.'}, response_class=HttpBadRequest)
		
		if length is not None:
			
			sentences = []
			for w in words:
				if (w.sentence.length<=int(length)):
					sentences.append(w.sentence)
			
			if len(sentences) < 1: return self.error_response(request, {'error': 'Wanna try it without sentence length condition?'}, response_class=HttpBadRequest)
		
			sentence = random.choice(sentences)
		
		else : 
			word = random.choice(words)
			sentence = word.sentence
		
		#data = self.build_bundle(obj=sentence, request=request)
		#data = self.full_dehydrate(data)
			
		data = {'sentence': sentence.__dict__}
		data = data['sentence']['_prop_values']
		data['words'] = []			
		for word in reversed(sentence.words.all()) :
			w = word.__dict__
			data['words'].append(w['_prop_values'])
										
		return self.create_response(request, data)	
		#return self.error_response(request, {'error': 'lemma and case are required.'}, response_class=HttpBadRequest)
		
	#/api/sentence/get_one_random_short/?format=json&case=gen&lemma=Λακεδαιμόνιος κρατέω
	def get_one_random_short(self, request, **kwargs):
		
		"""
		Gets one short random sentence of sentences with provided morphological information to one word.
		Makes sure the query params are still supported by the short sentence
		"""
		#query_params = {'case': 'gen', 'lemma': 'Λακεδαιμόνιος'}
		query_params = {}
		for obj in request.GET.keys():
			if obj in dir(Word) and request.GET.get(obj) is not None:
				query_params[obj] = request.GET.get(obj)
		
		# filter on params asap brings kinda performance, shuffle result set 						
		x = list(Word.objects.filter(**query_params))
		words = sorted(x, key=lambda *args: random.random())
		
		for word in words:
			sentence = word.sentence
			# asap check if the short sentence to a word's sentence returns a set with query params matching words
			sentence = sentence.get_shortened(query_params)
			if sentence is not None:
		
				data = {}
				data['words'] = []
				for word in sentence:
					w = word.__dict__
					data['words'].append(w['_prop_values'])
										
				return self.create_response(request, data)
		
		return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)
	
 		
class SentenceShortResource(ModelResource):
	"""
	Test resource for short sentence to parameter returns.
	"""
	file = fields.ForeignKey(DocumentResource, 'document')
	# expensive
	words = fields.ToManyField('api.api.WordResource', lambda bundle: Word.objects.filter(sentence__sentence=bundle.obj.sentence), null=True, blank=True)#, full = True)
		
	class Meta:
		queryset = Sentence.objects.filter()
		resource_name = 'sentence/short'
		always_return_data = True
		excludes = ['require_order', 'require_all', 'sentence']
		authorization = ReadOnlyAuthorization()
		filtering = {'internal': ALL,
					'CTS': ALL,
					'length': ALL,
					'file': ALL_WITH_RELATIONS,
					'words': ALL_WITH_RELATIONS}
		limit = 3
	
	def prepend_urls(self, *args, **kwargs):	
		
		return [
			url(r"^(?P<resource_name>%s)/%s%s$" % (self._meta.resource_name, 'get_one_random', trailing_slash()), self.wrap_view('get_one_random'), name="api_%s" % 'get_one_random'),
			]

	#/api/sentence/short/get_one_random/?format=json&lemma=Λακεδαιμόνιος
	#/api/sentence/short/get_one_random/?format=json&lemmaEnd=γος
	def get_one_random(self, request, **kwargs):
		
		"""
		Gets one random sentence of sentences with provided morphological information to one word.
		Makes sure the query params are still supported by the short sentence.
		"""
		#query_params = {'case': 'gen', 'lemma': 'Λακεδαιμόνιος'}
		query_params = {}
		for obj in request.GET.keys():
			if obj in dir(Word) and request.GET.get(obj) is not None:
				query_params[obj] = request.GET.get(obj)
		
		# filter on params asap brings kinda performance, shuffle result set 						
		#x = list(Word.objects.filter(**query_params))
		x = list(Word.objects.filter(lemma__ENDSWITH = request.GET.get('lemmaEnd')))
		words = sorted(x, key=lambda *args: random.random())
		
		for word in words:
			sentence = word.sentence
			# asap check if the short sentence to a word's sentence returns a set with query params matching words
			sentence = sentence.get_shortened(query_params)
			if sentence is not None:
		
				data = {}
				data['words'] = []
				for word in sentence:
					w = word.__dict__
					data['words'].append(w['_prop_values'])
										
				return self.create_response(request, data)
		
		return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)	
	
	
	class node():
		def __init__(self, value):
			self.value = value
			self.children = []
		
		def add_child(self, obj):
			self.children.append(obj)
	
	
	def shorten(self, wordArray, params = None):
		
		words = wordArray		
		# interrupt as soon as possible if there is no according syntactical information available
		try:
			words[0]['head']
		except:
			return None
			
		# save words within a map for faster lookup			
		nodes = dict((word['tbwid'], self.node(word)) for word in words)
		# build a "tree"
		verbs = []
		for w in words:
			if w['head'] is not 0:
	   			nodes[w['head']].add_child(nodes[w['tbwid']])
	   		if w['relation'] == "PRED" or w['relation'] == "PRED_CO":
	   			verbs.append(w)
														
		# start here 
		for verb in verbs:					
			# get the verb
			if verb['relation'] == "PRED" or verb['relation'] == "PRED_CO":						
				u, i = [], []
				aim_words = []
				# group the words and make sure, save the selected words
				for word in nodes[verb['tbwid']].children:
											
					if word.value['relation'] == "COORD":
						u.append(word.value['tbwid'])
						#aim_words.append(word)
						for w in nodes[word.value['tbwid']].children:
							if (w.value['relation'] == "OBJ_CO" or w.value['relation'] == "ADV_CO") and w.value['pos'] != "participle" and w.value['pos'] != "verb":
								i.append(w.value['tbwid'])
								aim_words.append(w.value)
					
					elif word.value['relation'] == "AuxP":
						aim_words.append(word.value)
						for w in nodes[word.value['tbwid']].children:
							if w.value['relation'] != "AuxC" and w.value['pos'] != "participle":
								aim_words.append(w.value)
					
								for w2 in nodes[w.value['tbwid']].children:
									if w2.value['relation'] == "ATR" and w2.value['pos'] != "verb":
										aim_words.append(w2.value)
										
										
					elif word.value['relation'] != "AuxC" and word.value['relation'] != "COORD" and word.value['pos'] != "participle":
						aim_words.append(word.value)
						for w in nodes[word.value['tbwid']].children:
							if w.value['relation'] == "ATR" and w.value['pos'] != "verb":
								aim_words.append(w.value)
					
				# refinement of u
				for id in u:
					for id2 in i:
						w = nodes[id2].value
						if w['head'] is id:
							aim_words.append(w)   
							
				aim_words.append(verb)
					
				# check if not verbs only are returned
				if len(aim_words) > 1:
					# consider params
					if len(params) > 0:
						# check if aim_words and parameter filtered intersect 
						cand = False
						for w in aim_words:
							for key in params:
								if w[key] == params[key]:
									cand = True
								else:
									cand = False
									continue
							
							if cand:										
								# set and order words
								return sorted(aim_words, key=lambda x: x['tbwid'], reverse=False)	
						
						return None		
					else:		
						# set and order words
						return sorted(aim_words, key=lambda x: x['tbwid'], reverse=False)
					
					
					# set and order words
					return sorted(aim_words, key=lambda x: x['tbwid'], reverse=False)
									
		return None

	
	def get_list(self, request, **kwargs):
		
		query_params = {}
		for obj in request.GET.keys():
			if obj in dir(Word) and request.GET.get(obj) is not None:
				query_params[obj] = request.GET.get(obj)
			#TODO: handl start with.. in cypher query
			# be later careful with contains, ends_with, startswith, not handle for now
			#elif obj.split('__')[0] in dir(Word) and request.GET.get(obj) is not None:
				#query_params[obj] = request.GET.get(obj)
		
		# if ordinary filter behavior is required, put key default
		if 'default' in request.GET.keys():		
			return super(SentenceResource, self).get_list(request, **kwargs)
		
		# filter word on parameters 
		#/api/sentence/?randomized=&format=json&lemma=κρατέω
		gdb = GraphDatabase("http://localhost:7474/db/data/")
		q = """START n=node(*) MATCH (w)-[:belongs_to]->(n) WHERE """
		if len(query_params) > 0:
			for key in query_params:
				q = q + """HAS (w.""" +key+ """) AND w.""" +key+ """='""" +query_params[key]+ """' AND """
			q = q[:len(q)-4]
			q = q + """RETURN w"""
				
		wordsTable = gdb.query(q)
		words = []
		# make it a nice table
		counter = 0
		for word in wordsTable.elements:
			words.append(wordsTable.elements[counter][0]['data'])
			counter+=1
												
		if len(wordsTable.elements) < 1:
			return self.error_response(request, {'error': 'No sentences hit your query.'}, response_class=HttpBadRequest)	
		
		data = {}
		data['sentences'] = []
		
		#/api/sentence/?randomized=&short=&format=json&lemma=κρατέω
		if 'randomized' in request.GET.keys():
						
			if 'short' in request.GET.keys():
				
				#randomize the words table	
						
				x = list(words)
				words = sorted(x, key=lambda *args: random.random())
		
				for word in words:
					# now get the words of this sentence as dict array
					sentTable = gdb.query("""START s=node(*) MATCH (w)-[:belongs_to]->(s) WHERE HAS (w.CTS) AND w.CTS = '""" +word['CTS']+ """' WITH s, w MATCH (w2)-[:belongs_to]->(s) RETURN w2""")
					sentence = []
					counter = 0
					for word in sentTable.elements:
						sentence.append(sentTable.elements[counter][0]['data'])
						counter+=1
					# and shorten it
					sentence = self.shorten(sentence, query_params)
					# asap check if the short sentence to a word's sentence returns a set with query params matching words
					if sentence is not None:
							
						tmp = {}
						tmp['words'] = sentence
						data['sentences'].append(tmp)														
						return self.create_response(request, data)
						
				return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)
			
			# randomized, long FIRST HANDLE STARTS/ENDS...
			#/api/sentence/?randomized=&format=json&lemma=κρατέω	
			else:
				
				word = random.choice(words)
				sentence = word.sentence
				
				tmp = {'sentence': sentence.__dict__}
				tmp = tmp['sentence']['_prop_values']
				tmp['words'] = []	
				for word in reversed(sentence.words.all()):			
					w = word.__dict__
					tmp['words'].append(w['_prop_values'])
				
				data['sentences'].append(tmp)
				return self.create_response(request, data)
		
		# not randomized -> CTS given
		else:
			# not randomized, short and CTS queries a sentence via CTS
			if 'short' in request.GET.keys():
				
				CTS = request.GET.get('CTS')
				#/api/sentence/?format=json&short=&form=ἀπέβησαν
				if CTS is None:				
					return self.error_response(request, {'error': 'CTS required.'}, response_class=HttpBadRequest)
				
				# not randomized, short with CTS
				#/api/sentence/?format=json&short=&CTS=urn:cts:greekLit:tlg0003.tlg001.perseus-grc1:1.108.5
				else:
					sentence = Sentence.objects.get(CTS = CTS)
					sentence = sentence.get_shortened({})
					
					tmp = {}
					tmp['words'] = []				
					for word in sentence:
						w = word.__dict__
						tmp['words'].append(w['_prop_values'])
						
					data['sentences'].append(tmp)					
					return self.create_response(request, data)
				
				return self.error_response(request, {'error': 'No short sentences hit your query.'}, response_class=HttpBadRequest)
			
			# not randomized, long, no CTS implies default or error
			else:
				CTS = request.GET.get('CTS')
				# if CTS is missed all sentences containing words that hit the query are returned
				#/api/sentence/?format=json&form=ἀπέβησαν&lemma__endswith=νω
				if CTS is None:					
					# and no other params -> default
					if len(query_params) < 1:	
						return super(SentenceResource, self).get_list(request, **kwargs)
				
					else:
						return self.error_response(request, {'error': 'CTS required or sentence parameters.'}, response_class=HttpBadRequest)
				
				# not randomized, long, CTS return one sentence
				#/api/sentence/?format=json&CTS=urn:cts:greekLit:tlg0003.tlg001.perseus-grc1:1.108.5
				else:
						
					sentence = Sentence.objects.get(CTS = CTS)
								
					tmp = {'sentence': sentence.__dict__}
					tmp = tmp['sentence']['_prop_values']
					tmp['words'] = []
					for word in reversed(sentence.words.all()):
						
						w = word.__dict__
						tmp['words'].append(w['_prop_values'])
					
					data['sentences'].append(tmp)
					
					return self.create_response(request, data)
				
				return self.error_response(request, {'error': 'No sentences hit your query.'}, response_class=HttpBadRequest)
	
		
class LemmaResource(ModelResource):
	
	# filtering on word object list is faster than on the bundle object; expensive anyway
	words = fields.ToManyField('api.api.WordResource', lambda bundle: Word.objects.filter(lemma=bundle.obj), null=True, blank=True)
	
	class Meta:
		queryset = Lemma.objects.all()
		resource_name = 'lemma'
		always_return_data = True
		excludes = ['require_order', 'require_all']
		authorization = ReadOnlyAuthorization()
		filtering = {'value': ALL,
					#'wordcount': ALL, # coming soon?
					'words': ALL_WITH_RELATIONS}


class TranslationResource(ModelResource):
	
	#sentenceRes = fields.ForeignKey(SentenceResource, 'sentence')#, full = True	
		
	class Meta:
		queryset = Word.objects.all()
		resource_name = 'translation'
		always_return_data = True
		excludes = ['require_all', 'sentence', 'case', 'cid', 'degree', 'dialect', 'form', 'gender', 'head', 'isIndecl', 
				'lemma', 'lemmas', 'mood', 'number' , 'person', 'pos', 'posAdd', 'posClass', 'ref', 'relation', 'tbwid', 'tense', 'translation', 'voice']		
		authorization = ReadOnlyAuthorization()
		filtering = {'internal': ALL,
					'CTS': ALL,
					'value': ALL,
					'length': ALL,					
					'sentenceRes': ALL_WITH_RELATIONS}


class WordResource(ModelResource):

	##base = fields.ToOneField('api.api.LemmaResource', lambda bundle: None if bundle.obj.lemmas is None else '' if bundle.obj.lemmas is '' else Lemma.objects.get(value=bundle.obj.lemmas), null=True, blank=True) 
	
	#word/?format=json&sentenceRes__file__lang=fas
	#sentenceRes = fields.ForeignKey(SentenceResource, 'sentence')#, full = True)
	#root = fields.ToOneField('api.api.LemmaResource', lambda bundle: None if bundle.obj.lemmas is None else Lemma.objects.get(value=bundle.obj.lemmas), null=True, blank=True)			
	
	#translation = fields.ToManyField('api.api.TranslationResource', attribute=lambda bundle: bundle.obj.translation.all(), null=True, blank=True)

	class Meta:
		queryset = Word.objects.all()
		resource_name = 'word'
		always_return_data = True
		excludes = ['require_all', 'sentence', 'children']
		authorization = ReadOnlyAuthorization()
		filtering = {'internal': ALL,
					'CTS': ALL,
					'value': ALL,
					'length': ALL,
					'form': ALL,
					'lemma': ALL,
					'pos': ALL, 
					'person': ALL,
					'number': ALL,
					'tense': ALL,
					'mood': ALL,
					'voice': ALL,
					'gender': ALL,
					'case': ALL,
					'degree': ALL,
					'dialect': ALL,
					'isIndecl': ALL,
					'posAdd': ALL,					
					'sentenceRes': ALL_WITH_RELATIONS,
					'base': ALL_WITH_RELATIONS}
		
	def build_filters(self, filters=None):
		"""
		A filter example to compose several conditions within a filer - this would make sense if we ask for a special (static) compos. more often
		this should MAYBE be more generic! 
		"""
		if filters is None:
			filters = {}
				 		
	 	orm_filters = super(WordResource, self).build_filters(filters)
	 	
	 	if 'q' in filters:
			#/api/word/?q=perseus-grc1
			orm_filters = {'CTS__contains':filters['q'], # comes from the url: dyn; contains greek words, that are masc. nouns
						'pos': "noun",
						'gender': "masc"}
		return orm_filters
			
		
					
	
	
	
	