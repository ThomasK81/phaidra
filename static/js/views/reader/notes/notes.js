define(['jquery', 
	'underscore', 
	'backbone', 
	'utils', 
	'views/reader/notes/about-word', 
	'views/reader/notes/translations', 
	'views/reader/notes/about-sentence', 
	'views/reader/notes/user-annotations', 
	'views/reader/notes/translate', 
	'views/reader/notes/build-parse-tree', 
	'text!/templates/js/reader/notes/notes.html'], 
	function($, _, Backbone, Utils, AboutWordView, TranslationsView, AboutSentenceView, 
	UserAnnotationsView, TranslateView, BuildParseTreeView, Template) { 

		return Backbone.View.extend({
			/**
			 * Notes view contains subviews that provide, essentially, different views of a word and its containing sentence.
			 * @module views/reader/notes/notes
			 */ 
			tagName: 'div', 
			className: 'col-md-12 notes',
			template: _.template(Template),
			events: { 
				'click #reader-menu a': 'navigate',
			},
			/** Takes options from parent Reader view. */
			initialize: function(options) {
				this.options = options;	

				this.topics = options.topics;
				this.model.words.bind('change:selected change:hovered', this.toggleNotes, this);

				// Contains references to our subviews
				this.currentView = 'about-word';
				this.views = [];
			},
			render: function() {
				this.$el.html(Template);
				this.$el.find('a').tooltip({ container: 'body' });
				return this;	
			},
			navigate: function(e) {

				// 'About' button has span inside, so need to select its parent sometimes
				e.target = (e.target.tagName === 'A') ? e.target : e.target.parentElement;
				var target = e.target.dataset.target;
				this.currentView = target;
				var els = this.$el.find('div[data-source]');

				$.each(els, function(i, el) {
					if (el.dataset.source === target)
						$(el).show();
					else 
						$(el).hide();
				});

				this.$el.find('#reader-menu li').removeClass('active');
				$(e.target.parentElement).addClass('active');
				this.renderSubview(target);
			},
			// TODO: Require each of these views only if requested
			makeView: function(view, options) {
				switch (view) {
					case 'about-word':
						return new AboutWordView(options);
					case 'about-sentence':
						return new AboutSentenceView(options);
					case 'user-annotations':
						return new UserAnnotationsView(options);
					case 'translate':
						return new TranslateView(options);
					case 'build-parse-tree':
						return new BuildParseTreeView(options);
					case 'translations':
						return new TranslationsView(options);
				}
			},
			renderSubview: function(view, options) {
				var selected = this.model.words.findWhere({ selected: true });

				// Renders the appropriate subview
				var subrender = function() {
					
					// Give the user the definite article with the definition
					if (selected.get('pos') == 'noun') {
						selected.set('article', Utils.getDefiniteArticle(selected.get('gender')));
					}

					var options = {
						el: this.$el.find('div[data-source="' + view + '"]'),
						model: selected,
						collection: this.model,
						langs: _.uniq(_.pluck(selected.get('translations'), 'lang')),
						lang: this.lang || LOCALE 	// Locale is set in base.html, comes from Django. 
					};

					// TODO: Find out what the best way to do this is -- currently, when creating new views, Backbone
					// prerves event handles from views that have been overwritten, and re-binds on each creation.
					if (this.views[view]) {
						this.views[view].options = options;
						this.views[view].model = options.model;
						this.views[view].collection = options.collection;
						this.views[view].render();
					}
					else {
						this.views[view] = this.makeView(view, options).render();
					}

					// Open it up
					this.$el.addClass('expanded');

				}.bind(this);

				// Fetch word's details, or if we've fetched them before, simply display
				if (selected.get('translated')) subrender();
				else selected.fetch({ success: subrender });

			},
			toggleNotes: function(model) {
				// They're simply hovering over a word
				if (model.get('hovered') && !model.get('selected') && 
					(this.model.words.findWhere({ selected: true }) == undefined)) {
					this.$el.find('.intro').html(
						gettext('Information about') + ' <span lang="' + model.get('lang') +'">' + model.get('value') + '</span>'
					);
					this.$el.removeClass('expanded');
					this.$el.find('.notes-nav a').eq(1).attr('title', 'Hide Resources');
				}
				// This will perform a fetch on the model for full data
				else if (model.get('selected')) {
					this.$el.find('.notes-nav a').eq(1).attr('title', gettext('Show Resources'));
					this.renderSubview(this.currentView);
				}

			},
			collapseNotes: function(e) {
				e.preventDefault();
				var selected = this.model.words.findWhere({ selected: true });
				if (typeof(selected) != 'undefined')
					selected.set('selected', false);
			}
		});
	}
);
