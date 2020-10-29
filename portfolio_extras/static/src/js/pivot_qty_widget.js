odoo.define('web.pivot_qty_widget', function (require) {
    'use strict';

    var field_utils = require('web.field_utils');
    var registry = require('web.field_registry');
    var FieldInteger = require('web.basic_fields').FieldInteger;

    let FieldIntegerPivot = FieldInteger.extend({
        _renderReadonly: function () {
            this.$el.html(this._formatValue(this.value));
        },
    });
    registry.add('pivot_qty_widget', FieldIntegerPivot);

    field_utils.format['pivot_qty_widget'] = function formatInteger(value, field, options) {
        if (value)
            return '<div style="width: 50px; color:red; background: #ffd3db; text-align: center;">' + value + '</div>';
        return '<div style="width: 50px; color:green; background: #d3ffd8; text-align: center;">OK</div>';
    };

    field_utils.format['pivot_qty_simple'] = function formatInteger(value, field, options) {
        if (value === 0) {
            return '';
        }
        return '' + value;
    };
});