# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api
from datetime import date

_logger = logging.getLogger(__name__)


class PortfolioReportWizard(models.TransientModel):
    _name = 'portfolio_extras.portfolio_report_wizard'
    _description = 'Wizard for portfolio quantities report'

    portfolio_selection = fields.Selection([
        (0, 'All portfolios'),
        (1, 'A specific portfolio'),
        (2, 'Several portfolios')
    ])
    portfolio = fields.Many2one('x_tw_portefeuille')
    search = fields.Char('Filter portfolios by name')

    report_selection = fields.Selection([
        (0, 'Check affectations vs. bottling'),
        (1, 'Check stocks')
    ], default=1)

    def compute(self):

        self.ensure_one()
        self.compute_report(self.portfolio_selection, self.portfolio, self.search)

        if self.report_selection == 0:
            action = self.env.ref('portfolio_extras.action_report_list').read()[0]
        else:
            action = self.env.ref('portfolio_extras.action_report_details_list').read()[0]

        if self.portfolio_selection == 1:
            action['domain'] = [('portfolio', '=', self.portfolio.id)]
        elif self.portfolio_selection == 2:
            action['domain'] = [('portfolio', 'ilike', self.search)]

        return action

    @api.model
    def compute_report(self, portfolio_selection=0, portfolio=None, search=None):
        pf_domain = []
        if portfolio_selection == 1 and portfolio:
            pf_domain = [('id', '=', portfolio.id)]
        elif portfolio_selection == 2:
            pf_domain = [('x_name', 'ilike', search)]

        portfolios = self.env['x_tw_portefeuille'].search(pf_domain)

        if portfolio_selection == 0:
            # don't use theses: too slow
            # self.env['portfolio_extras.portfolio_report'].search([]).unlink()
            # self.env['portfolio_extras.portfolio_report_line'].search([]).unlink()
            self.env.cr.execute('TRUNCATE TABLE %s CASCADE;' % self.env['portfolio_extras.portfolio_report']._table)
            self.env.cr.execute(
                'TRUNCATE TABLE %s CASCADE;' % self.env['portfolio_extras.portfolio_report_line']._table)
        elif portfolios:
            # self.env['portfolio_extras.portfolio_report'].search([('portfolio', 'in', portfolios.ids)]).unlink()
            self.env.cr.execute('DELETE FROM %s WHERE portfolio in (%s)' %
                                (self.env['portfolio_extras.portfolio_report']._table,
                                 ','.join(map(str, portfolios.ids))))

        valuation_cache = {}

        # additional valo by bt.
        bottling_bonus_cache = {}

        def valuate(t):
            if t not in valuation_cache:
                valuation_cache[t] = self.env['portfolio_extras.valuation'].get_uw_valuations(t)
            return valuation_cache[t]

        for pf in portfolios:
            _logger.debug('Report for %s' % pf.x_name)

            total = {}
            insertable = []

            for aff in pf.x_studio_tw_portefeuille_ligne_affect:
                tid = aff.x_studio_tw_portefeuille_ligne_affect_modele_article.id
                total.setdefault(tid, {})
                total[tid].setdefault('affected_bt', 0)
                total[tid]['affected_bt'] += aff.x_studio_tw_portefeuille_ligne_affect_quantite_eq75
                total[tid]['price'] = aff.x_studio_tw_portefeuille_ligne_affect_prix_achat

            for mise in pf.x_studio_tw_portefeuille_ligne_mise:
                tid = mise.x_studio_tw_portefeuille_ligne_mise_modele_article.id
                mid = mise.x_studio_tw_portefeuille_ligne_mise_mise
                bottling_bonus_cache[mid.id] = (mid.x_bonus_val or 0) * mid.x_studio_tw_valeur_nombre_col
                pid = mise.x_studio_tw_portefeuille_ligne_mise_modele_article._get_variant_id_for_combination(mid)
                if not pid:
                    print('No variant found for', mise.x_studio_tw_portefeuille_ligne_mise_mise, mid)
                valuate(tid)

                total.setdefault(tid, {})
                total[tid].setdefault('details', {})
                total[tid]['details'].setdefault(pid, {})
                total[tid]['details'][pid]['bottling'] = mid.id
                total[tid]['details'][pid]['format'] = mid.x_studio_tw_valeur_equivalent75_par_col
                total[tid]['details'][pid]['price'] = mise.x_studio_tw_portefeuille_ligne_mise_prix
                total[tid]['details'][pid].setdefault('bottling_qty', 0)
                total[tid]['details'][pid].setdefault('bottling_eq75_qty', 0)
                total[tid]['details'][pid]['bottling_qty'] += mise.x_studio_tw_portefeuille_ligne_mise_quantite_col
                total[tid]['details'][pid]['bottling_eq75_qty'] += \
                    mise.x_studio_tw_portefeuille_ligne_mise_quantite_eq75

            for recep in pf.x_studio_tw_portefeuille_ligne_a_receptionner:
                pid = recep.product_id.id
                tid = recep.product_id.product_tmpl_id.id
                m = recep.product_id.attribute_value_ids[0]
                mid = m.id
                valuate(tid)

                total.setdefault(tid, {})
                total[tid].setdefault('details', {})

                # special case for CS:
                # attach missing mises to compatible one we sold to the client
                if pf.x_studio_tw_portefeuille_type == 'storage' and pid not in total[tid]['details']:
                    for ppid, vals in total[tid]['details'].items():
                        if vals.get('format', 0) == m.x_studio_tw_valeur_equivalent75_par_col:
                            pid = ppid
                            mid = vals.get('bottling')
                            break

                total[tid]['details'].setdefault(pid, {})
                total[tid]['details'][pid].setdefault('to_receive_qty', 0)
                total[tid]['details'][pid]['bottling'] = mid
                total[tid]['details'][pid]['to_receive_qty'] += recep.product_uom_qty

            for stock in pf.x_studio_tw_stock_quant_portefeuille:
                pid = stock.product_id.id
                tid = stock.product_id.product_tmpl_id.id
                m = stock.product_id.attribute_value_ids[0]
                mid = m.id
                valuate(tid)

                total.setdefault(tid, {})
                total[tid].setdefault('details', {})

                # special case for CS:
                # attach missing mises to compatible one we sold to the client
                if pf.x_studio_tw_portefeuille_type == 'storage' and pid not in total[tid]['details']:
                    for ppid, vals in total[tid]['details'].items():
                        if vals.get('format', 0) == m.x_studio_tw_valeur_equivalent75_par_col:
                            pid = ppid
                            mid = vals.get('bottling')
                            break

                total[tid]['details'].setdefault(pid, {})
                total[tid]['details'][pid].setdefault('in_stock_qty', 0)
                total[tid]['details'][pid]['bottling'] = mid
                total[tid]['details'][pid]['in_stock_qty'] += stock.quantity

            for des in pf.x_studio_tw_portefeuille_desinvestissement:
                if des.x_studio_field_FXKRw == 'done':
                    pid = des.x_studio_tw_desinvestissement_article.id
                    tid = des.x_studio_tw_desinvestissement_article.product_tmpl_id.id
                    qty = des.x_studio_tw_desinvestissement_quantite_col
                    price = des.x_studio_tw_desinvestissement_prix_vente_unitaire

                    total.setdefault(tid, {})
                    total[tid].setdefault('details', {})
                    total[tid]['details'].setdefault(pid, {})
                    total[tid]['details'][pid].setdefault('divested_qty', 0)
                    total[tid]['details'][pid].setdefault('consumed_qty', 0)
                    total[tid]['details'][pid].setdefault('divested_sum', 0)
                    total[tid]['details'][pid].setdefault('consumed_sum', 0)

                    if des.x_studio_tw_desinvestissement_type == 'consommation':
                        val = self.env['portfolio_extras.valuation'].get_uw_valuations(
                            tid, at_date=des.x_studio_tw_desinvestissement_date_vente)[2]
                        total[tid]['details'][pid]['consumed_qty'] += qty
                        total[tid]['details'][pid]['consumed_sum'] += (qty * val)
                    else:
                        total[tid]['details'][pid]['divested_qty'] += qty
                        total[tid]['details'][pid]['divested_sum'] += (qty * price)
                else:
                    pid = des.x_studio_tw_desinvestissement_article.id
                    tid = des.x_studio_tw_desinvestissement_article.product_tmpl_id.id
                    qty = des.x_studio_tw_desinvestissement_quantite_col

                    total.setdefault(tid, {})
                    total[tid].setdefault('details', {})
                    total[tid]['details'].setdefault(pid, {})
                    total[tid]['details'][pid].setdefault('to_divest_qty', 0)
                    total[tid]['details'][pid].setdefault('to_consume_qty', 0)

                    if des.x_studio_tw_desinvestissement_type == 'consommation':
                        total[tid]['details'][pid]['to_consume_qty'] += qty
                    else:
                        total[tid]['details'][pid]['to_divest_qty'] += qty

            for tid, details in total.items():
                lines = []
                val = valuation_cache.get(tid, (0, 0, 0))
                for pid, d in details.get('details', {}).items():
                    pval = tuple([(v * d.get('bottling_eq75_qty', 0) / (d.get('bottling_qty', 1) or 1) or d.get('price', 0))
                                  + bottling_bonus_cache.get(d.get('bottling', 0), 0) for v in val])
                    sold_val = 0
                    if d.get('divested_qty', 0) > 0:
                        sold_val = d.get('divested_sum', 0) / d['divested_qty']
                    consumed_val = 0
                    if d.get('consumed_qty', 0) > 0:
                        consumed_val = d.get('consumed_sum', 0) / d['consumed_qty']
                    lines.append({
                        'product': pid,
                        'bottling': d.get('bottling', None),
                        'price': d.get('price', 0),
                        'bottling_qty': d.get('bottling_qty', 0),
                        'bottling_eq75_qty': d.get('bottling_eq75_qty', 0),
                        'to_receive_qty': d.get('to_receive_qty', 0),
                        'in_stock_qty': max(0, d.get('in_stock_qty', 0) - d.get('to_divest_qty', 0) - d.get('to_consume_qty', 0)),
                        'to_divest_qty': d.get('to_divest_qty', 0),
                        'to_consume_qty': d.get('to_consume_qty', 0),
                        'divested_qty': d.get('divested_qty', 0),
                        'consumed_qty': d.get('consumed_qty', 0),
                        'valuation_l': (pval[0] or d.get('price', 0)),
                        'valuation_m': (pval[1] or d.get('price', 0)),
                        'valuation_h': (pval[2] or d.get('price', 0)),
                        'divested_valuation': sold_val,
                        'consumed_valuation': consumed_val,
                    })

                insertable.append({
                    'portfolio': pf.id,
                    'product_tmpl': tid,
                    'affected_eq75': details.get('affected_bt', 0),
                    'price': details.get('price', 0),
                    'details': [(0, False, l) for l in lines]
                })

            self.env['portfolio_extras.portfolio_report'].create(insertable)


class PortfolioReport(models.Model):
    _name = 'portfolio_extras.portfolio_report'
    _description = 'Portfolio quantities report'

    portfolio = fields.Many2one('x_tw_portefeuille', index=True, ondelete='cascade')
    product_tmpl = fields.Many2one('product.template', string='Product Template', index=True, ondelete='cascade')
    affected_eq75 = fields.Integer(string='Affected eq. 75')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.ref('base.EUR'))
    price = fields.Monetary('Buying price')

    details = fields.One2many('portfolio_extras.portfolio_report_line', 'report')
    bottled_eq75 = fields.Integer(string='Bottling eq. 75', compute='_bottled_eq75', store=True)
    diff = fields.Integer(string="Difference (aff. vs. bottling)", compute='_diff', store=True)
    diff_bottling = fields.Integer(string="Difference (bottling vs. stocks)", compute='_diff_bottling', store=True)

    @api.depends('details.bottling_eq75_qty')
    def _bottled_eq75(self):
        for record in self:
            record.bottled_eq75 = sum([d.bottling_eq75_qty for d in record.details])

    @api.depends('bottled_eq75', 'affected_eq75')
    def _diff(self):
        for record in self:
            record.diff = abs(record.bottled_eq75 - record.affected_eq75)

    @api.depends('details')
    def _diff_bottling(self):
        for record in self:
            record.diff_bottling = sum([d.diff for d in record.details])


class PortfolioReportLine(models.Model):
    _name = 'portfolio_extras.portfolio_report_line'
    _description = 'Portfolio quantities report details'

    # lines are children of portfolio_report for a given portfolio et product_template

    report = fields.Many2one('portfolio_extras.portfolio_report', index=True, ondelete='cascade')
    portfolio = fields.Many2one('x_tw_portefeuille', related='report.portfolio', readonly=True, store=True, index=True)
    portfolio_type = fields.Many2one('x_tw_portefeuille', related='report.portfolio.x_studio_tw_portefeuille_type', readonly=True, store=True, index=True)
    product = fields.Many2one('product.product', ondelete='cascade')
    product_template = fields.Many2one('product.template', related='product.product_tmpl_id', readonly=True, store=True, index=True)
    bottling = fields.Many2one('product.attribute.value')

    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.ref('base.EUR'))
    price = fields.Monetary('Entry price')
    valuation_l = fields.Monetary('Valuation (low)')
    valuation_m = fields.Monetary('Valuation (median)')
    valuation_h = fields.Monetary('Valuation (high)')
    divested_valuation = fields.Monetary('Average valuation (sold)')
    consumed_valuation = fields.Monetary('Valuation (consumed)')

    perf_l = fields.Float('Raw perf (low)', compute='_compute_perf')
    perf_m = fields.Monetary('Raw perf (median)', compute='_compute_perf')
    perf_h = fields.Monetary('Raw perf (high)', compute='_compute_perf')
    perf_divested = fields.Monetary('Raw perf (sold)', compute='_compute_perf')

    bottling_qty = fields.Integer(string='Theoretical')
    bottling_eq75_qty = fields.Integer(string='Bottling EQ75')
    to_receive_qty = fields.Integer(string='To receive')
    in_stock_qty = fields.Integer(string='Stock')
    to_divest_qty = fields.Integer(string='Stock to divest')
    to_consume_qty = fields.Integer(string='Stock to deliver')
    divested_qty = fields.Integer(string='Sold')
    consumed_qty = fields.Integer(string='Consumed')
    diff = fields.Integer(string="Difference", compute='_diff', store=True)
    no_valuation = fields.Boolean(string="No valuation", compute='_no_valuation', store=True)

    @api.depends('bottling_qty', 'to_receive_qty', 'in_stock_qty', 'divested_qty', 'consumed_qty')
    def _diff(self):
        for record in self:
            record.diff = abs((record.to_receive_qty +
                               record.in_stock_qty +
                               record.to_divest_qty +
                               record.to_consume_qty +
                               record.divested_qty +
                               record.consumed_qty) - record.bottling_qty)

    @api.depends('price', 'valuation_l', 'valuation_h')
    def _no_valuation(self):
        for record in self:
            record.no_valuation = (record.price == record.valuation_l and record.price == record.valuation_h)

    @api.depends('price', 'valuation_l', 'valuation_m', 'valuation_h', 'divested_valuation')
    def _compute_perf(self):
        for rec in self:
            entry_price = rec['price']
            if entry_price:
                rec['perf_l'] = (rec['valuation_l'] - entry_price) / entry_price
                rec['perf_m'] = (rec['valuation_m'] - entry_price) / entry_price
                rec['perf_h'] = (rec['valuation_h'] - entry_price) / entry_price
                rec['perf_divested'] = (rec['divested_valuation'] - entry_price) / entry_price
            else:
                rec['perf_l'] = 0
                rec['perf_m'] = 0
                rec['perf_h'] = 0
                rec['perf_divested'] = 0

