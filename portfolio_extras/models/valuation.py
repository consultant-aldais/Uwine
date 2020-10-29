import logging
import time
from pprint import pprint

from dateutil.relativedelta import relativedelta

from odoo import models, fields, api
from datetime import date, datetime

_logger = logging.getLogger(__name__)

NB_YEAR_FIRST_QUOTATION = 3
MONTH_FIRST_QUOTATION = 6  # June

QUOTATION_DURATION_MONTHS = 54
QUOTATION_VALUE_ONE_MONTH = 0.6
QUOTATION_LOW = 0.92
QUOTATION_HIGH = 1.27


class ValuationWizard(models.TransientModel):
    _name = 'portfolio_extras.valuation_wizard'
    _description = 'Wizard for updating valuations'

    valuation_selection = fields.Selection([
        (0, 'All valuations'),
        (1, 'A product'),
        (2, 'Specific date(s)'),
    ])
    product_template = fields.Many2one('product.template', domain=[('categ_id', '=', 2)])
    date_min = fields.Date('From')
    date_max = fields.Date('To')

    report_selection = fields.Selection([
        (0, 'Check everything: affectations, bottling and stocks'),
        (1, 'Check bottling vs. stocks')
    ])

    def compute(self):
        self.ensure_one()

        product_template = None
        date_min = None
        date_max = None
        if self.valuation_selection == 1:
            product_template = self.product_template.id
        if self.valuation_selection == 2:
            date_min = self.date_min
            date_max = self.date_max
        self.env['portfolio_extras.valuation'].make_valuations(product_template=product_template,
                                                               date_min=date_min,
                                                               date_max=date_max)

        return self.env.ref('portfolio_extras.action_valuation_list').read()[0]


class Valuation(models.Model):
    _name = 'portfolio_extras.valuation'
    _description = 'Valuation'
    _order = 'date desc'

    name = fields.Char(compute='_compute_name')
    product_template = fields.Many2one('product.template', required=True, index=True)
    date = fields.Date(required=True, index=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.ref('base.EUR'))
    pp = fields.Monetary('Prix place', default=0)
    b2c = fields.Monetary('B2C price', default=0)
    b2c_hk = fields.Monetary('B2C price (HK)', default=0)

    @api.model
    def _month(self, d):
        if isinstance(d, datetime):
            return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if isinstance(d, date):
            return d.replace(day=1)
        return d

    def _compute_name(self):
        for rec in self:
            rec['name'] = rec['product_template']['name'] + '/' + str(rec['date'])

    @api.onchange('date')
    def on_date_changed(self):
        self.date = self._month(self.date)

    @api.model
    def get_pp_valuation(self, product_template_id, at_date=None):
        if not at_date:
            at_date = date.today()
        at_date = self._month(at_date)

        vals = self.search_read([
            ('product_template', '=', product_template_id),
            ('date', '<=', at_date),
            ('pp', '>', 0)
        ],  fields=['pp'], limit=1, order='date desc')
        if vals:
            return vals[0]['pp']
        return 0

    @api.model
    def get_b2c_valuation(self, product_template_id, at_date=None):
        if not at_date:
            at_date = date.today()
        at_date = self._month(at_date)

        b2c = 0
        b2c_hk = 0

        vals = self.search_read([
            ('product_template', '=', product_template_id),
            ('date', '<=', at_date),
            ('b2c', '>', 0)
        ], fields=['b2c'], limit=1, order='date desc')
        if vals:
            b2c = vals[0]['b2c']

        # we need to make 2 distinct requests, because b2c and b2c_hk may not be on the same
        # record (if their valuation date is different)
        vals = self.search_read([
            ('product_template', '=', product_template_id),
            ('date', '<=', at_date),
            ('b2c_hk', '>', 0)
        ], fields=['b2c_hk'], limit=1, order='date desc')
        if vals:
            b2c_hk = vals[0]['b2c_hk']

        return b2c, b2c_hk

    @api.model
    def get_uw_valuations(self, product_template_id, at_date=None):

        if not at_date:
            at_date = date.today()
        at_date = self._month(at_date)

        val = self.get_pp_valuation(product_template_id, at_date)
        low = val * QUOTATION_LOW
        high = low * QUOTATION_HIGH
        uwine_quotation = low

        # A bit of explanation:
        # The U'Wine quotation starts at LOW + 34.6% * (HIGH - LOW),
        # and slowly converges to LOW in 54 months (0.6% each month)
        # After 54 months, the quotation is LOW

        diff_hl = high - low
        templates = self.env['product.template'].browse([product_template_id])
        if templates:
            try:
                vintage = int(templates[0]['x_studio_tw_template_millesime'])
                year = vintage + NB_YEAR_FIRST_QUOTATION
                start_quotation = date(year, MONTH_FIRST_QUOTATION, 1)
                end_quotation = start_quotation + relativedelta(months=QUOTATION_DURATION_MONTHS)
                if at_date >= start_quotation:
                    if at_date < end_quotation:
                        diff = relativedelta(end_quotation, at_date)
                        nb_month_left = 12 * diff.years + diff.months + diff.days / 30
                        gap_percentage = nb_month_left * QUOTATION_VALUE_ONE_MONTH
                        if nb_month_left > 0:
                            uwine_quotation += gap_percentage * diff_hl / 100
                else:
                    uwine_quotation += diff_hl * (QUOTATION_VALUE_ONE_MONTH * QUOTATION_DURATION_MONTHS) / 100
            except ValueError:
                pass

        return low, uwine_quotation, high

    @api.model
    def get_all_valuations(self, product_template_id, at_date=None):

        low, uw, high = self.get_uw_valuations(product_template_id, at_date=at_date)
        b2c, b2c_hk = self.get_b2c_valuation(product_template_id, at_date=at_date)

        return {
            'low': low,         # PP - 8%
            'uwine': uw,        # U'Wine quotation: high -> low
            'high': high,       # low + 27%
            'b2c': b2c,         # Wine Decider b2c price in France
            'b2c_hk': b2c_hk    # Wine Decider b2c price in HK
        }

    @api.model
    def make_valuations(self, product_template=None, date_min=None, date_max=None):
        mins = {}

        domain = []
        domain2 = []
        if product_template:
            domain.append(('x_studio_tw_valoristation_article_article', '=', product_template))
            domain2.append(('product_template', '=', product_template))
        if date_min:
            domain.append(('x_studio_tw_valoristation_article_date_valo', '>=', date_min))
            domain2.append(('date', '>=', date_min))
        if date_max:
            domain.append(('x_studio_tw_valoristation_article_date_valo', '<=', date_max))
            domain2.append(('date', '<=', date_max))

        start_time = time.time()
        sources = self.env['x_valorisation_article'].search_read(
            domain=domain,
            fields=['x_studio_tw_valoristation_article_article',
                    'x_studio_tw_valoristation_article_date_valo',
                    'x_studio_tw_valoristation_article_prix',
                    'x_studio_tw_valoristation_article_prix_b2c',
                    'x_studio_tw_valoristation_article_prix_b2c_hk'],
            order='x_studio_tw_valoristation_article_article, x_studio_tw_valoristation_article_date_valo')

        for s in sources:
            pr = s['x_studio_tw_valoristation_article_article'][0]
            d = self._month(s['x_studio_tw_valoristation_article_date_valo'])
            b2b = s['x_studio_tw_valoristation_article_prix']
            b2c = s['x_studio_tw_valoristation_article_prix_b2c']
            b2c_hk = s['x_studio_tw_valoristation_article_prix_b2c_hk']
            mins.setdefault(pr, {}).setdefault(d, {'b2b': None, 'b2c': None, 'b2c_hk': None})

            if b2b and (not mins[pr][d]['b2b'] or mins[pr][d]['b2b'] > b2b):
                mins[pr][d]['b2b'] = b2b
            if b2c and (not mins[pr][d]['b2c'] or mins[pr][d]['b2c'] > b2c):
                mins[pr][d]['b2c'] = b2c
            if b2c_hk and (not mins[pr][d]['b2c_hk'] or mins[pr][d]['b2c_hk'] > b2c_hk):
                mins[pr][d]['b2c_hk'] = b2c_hk

        # print('Parsed %d in %0.2f s' % (len(sources), time.time() - start_time))

        start_time = time.time()
        self.search(domain2).unlink()
        # print('Deleted previous in %0.2f s' % (time.time() - start_time))

        start_time = time.time()
        lines = []
        for pr, vals in mins.items():
            for d, v in vals.items():
                lines.append({
                    'product_template': pr,
                    'date': d,
                    'pp': v.get('b2b', 0),
                    'b2c': v.get('b2c', 0),
                    'b2c_hk': v.get('b2c_hk', 0),
                })
        self.create(lines)
        # print('Created %d lines in %0.2f s' % (len(lines), time.time() - start_time))


