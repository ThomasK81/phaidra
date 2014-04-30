define(['jquery', 'underscore', 'backbone', 'text!templates/page.html'], function($, _, Backbone, PageTemplate) { 

	var View = Backbone.View.extend({
		events: { 
			'click .corner a': 'turnPage',
			'mouseenter .page-content span': 'hoverWord',
			'mouseleave .page-content span': 'hoverWord',
			'click .page-content span': 'clickWord'
		},
		tagName: 'div',
		className: 'col-md-12',
		template: _.template(PageTemplate),
		initialize: function(options) {
			this.options = options;
			this.render();
			this.turnPage();
		},
		render: function() {
			this.$el.html(this.template({ side: this.options.side, words: this.collection.models })); 
			return this;	
		},
		turnPage: function(cts) {
			var that = this;

			if (typeof(cts) == 'undefined')
				cts = 'urn:cts:greekLit:tlg0003.tlg001.perseus-grc1:1.89.2';

			$.ajax({
				url: '/api/v1/sentence/?format=json&CTS=' + cts,
				dataType: 'json',
				success: function(response) {
					var text = that.prepText(response.objects[0]);
					var ref = response.objects[0]["CTS"].split(':');

					that.$el.find('h1 a').html(ref[ref.length-1]);
				},
				error: function() {
					that.$el.find('.page-content').html('Error was encountered trying to render this page.');
				}
			});
		},
		/*
		* Builds up our collection of words for this page 
		*/
		prepText: function(text) {
			var that = this;
			var tokens = $.trim(text.sentence).split(' ');

			$.each(tokens, function(i, token) {
				that.collection.add({
					'value': token,
					'lang': 'grc',
					'CTS': text.CTS + ':' + (i + 1)
				});
			});

			this.render();
		},

		// TODO: Delegate these responsibilities to a super tiny word view 

		/*
		*	Change the 'hover' state of the model appropriately.
		*/
		hoverWord: function(e) {
			var model = this.collection.findWhere({ 
				CTS: $(e.target).attr('data-cts') 
			});

			var hovered = (e.type == 'mouseenter') ? true : false;
			model.set('hovered', hovered);
		},
		clickWord: function(e) {
			// See if any word is previously selected
			var prev = this.collection.findWhere({
				selected: true
			});
			var model = this.collection.findWhere({ 
				CTS: $(e.target).attr('data-cts') 
			});

			// If this word is the same as current word, deselect
			if (model == prev) {
				prev.set('selected', false);
				this.$el.find('.page-content span[data-cts="' + prev.get('CTS') + '"]').removeClass('selected');
			}
			else if (typeof(prev) != 'undefined') {
				prev.set('selected', false);
				this.$el.find('.page-content span[data-cts="' + prev.get('CTS') + '"]').removeClass('selected');
				model.set('selected', true);
				$(e.target).addClass('selected');
			}
			else {
				model.set('selected', true);
				$(e.target).addClass('selected');
			}
		}
	});

	return View;
});
