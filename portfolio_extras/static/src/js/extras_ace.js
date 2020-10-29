odoo.define("portfolio_extras.ace", function (require) {
    "use strict";

    var AceEditor = require('web_editor.ace');
    AceEditor.include({

        init: function() {
            this._super.apply(this, arguments);

            // force loading ext-language_tools.js to enable autocompletion
            this.jsLibs.push(['/portfolio_extras/static/src/js/lib/ace/ext-language_tools.js']);
        },

        start: function () {
            this._super.apply(this, arguments);
            window.ace.config.set('basePath', '/portfolio_extras/static/src/js/lib/ace');
            window.ace.config.set('themePath', '/portfolio_extras/static/src/js/lib/ace');
            window.ace.config.set('modePath', '/portfolio_extras/static/src/js/lib/ace');
            window.ace.config.set('workerPath', '/portfolio_extras/static/src/js/lib/ace');

            // enable autocompletion
            window.ace.require("ace/ext/language_tools");
            this.aceEditor.setOptions({
                enableLiveAutocompletion: true
            });
        }
    });
});
