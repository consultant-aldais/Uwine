import logging
from datetime import date

from odoo import models, api, fields
from odoo.addons.mail.wizard.mail_compose_message import _reopen

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    D2V = 0
    D2VSD = 0
    UWV = 0

    # check if all products of the order have been delivered
    all_delivered = fields.Boolean(compute='_all_delivered', store=True, readonly=True)

    def _read_locations(self):
        if not self.D2V:
            self.D2V = self.env.ref('__import__.stock_location_d2v').id
            self.D2VSD = self.env.ref('__import__.stock_location_d2vsd').id
            self.UWV = self.env.ref('__import__.stock_location_uwv').id

    @api.model
    def create(self, vals):
        if vals.get('x_studio_tw_commande_vente_portefeuille') and not vals.get('analytic_account_id'):
            pf = self.env['x_tw_portefeuille'].browse(vals.get('x_studio_tw_commande_vente_portefeuille'))

            account = self.env['account.analytic.account'].account_for_pf(pf)
            if account:
                vals['analytic_account_id'] = account

        result = super(SaleOrder, self).create(vals)
        return result

    def _fill_used_divests(self, info, qty, divests):
        used_divests = []
        remaining = qty
        self._read_locations()
        for divest in divests:

            stocks = self.env['stock.quant'].search([
                ('product_id', '=', divest.x_studio_tw_desinvestissement_article.id),
                ('owner_id', '=',
                    divest.x_studio_tw_desinvestissement_portefeuille.x_studio_tw_portefeuille_proprietaire.id),
                ('location_id', 'in', [self.D2V, self.D2VSD, self.UWV]),
                ('quantity', '>', 0)
            ])

            # we may have a divestment for 9 bt, with 3 in D2V, 3 in D2VSD and 3 in WINE+
            # -> we can only take 6 bt in this divestment
            for s in stocks:
                take_num = min(remaining, s.quantity, divest.x_studio_tw_desinvestissement_quantite_col)
                if take_num > 0:
                    remaining -= divest.x_studio_tw_desinvestissement_quantite_col
                    used_divests.append((divest, take_num, s.location_id.id))
                if remaining <= 0:
                    remaining = 0
                    break
            if remaining <= 0:
                remaining = 0
                break

        return remaining, used_divests

    def _confirm_picking(self, info, po):
        if po.picking_ids:
            sp = po.picking_ids[0]
            for line in sp.move_lines:
                for line_move in line.move_line_ids:
                    line_move.qty_done = line.product_uom_qty
            sp.button_validate()
        else:
            info.append(self._info_print('Can\'t find stock.picking'))

    def _confirm_purchase_order(self, info, divest):
        po = divest.x_studio_tw_desinvestissement_commande_achat
        po.button_confirm()
        self._confirm_picking(info, po)

    def _recrate_if_needed(self, info, line, current_divest, location_id):
        if line.product_id.attribute_value_ids[0].id != current_divest.x_studio_tw_desinvestissement_article.attribute_value_ids[0].id:
            if location_id:
                recrating = self.env['x_recaissage'].create({
                    'x_studio_location': location_id,
                    'x_studio_product': current_divest.x_studio_tw_desinvestissement_article.id,
                    'x_studio_cols': current_divest.x_studio_tw_desinvestissement_quantite_col,
                    'x_studio_mise': line.product_id.attribute_value_ids[0].id
                })
                info.append(self._info_print('Recrating <a href="#" data-oe-model="x_recaissage" data-oe-id="' +
                                             str(recrating.id) + '">' + recrating.x_name + '</a>'))
                info.append(self._info_print(str(current_divest.x_studio_tw_desinvestissement_quantite_col) + ' x ' +
                                             current_divest.x_studio_tw_desinvestissement_article.display_name +
                                             ' <span class="fa fa-long-arrow-right"></span> ' +
                                             line.product_id.display_name))
                self.env['ir.actions.server'].with_context(active_id=recrating.id,
                                                           active_model='x_recaissage',
                                                           active_ids=recrating.ids).browse([590]).run()

    def _info_print(self, info, newline=True):
        _logger.debug(info)
        if newline:
            return info + '<br>'
        return info

    def _perform_divests(self, info, line, used_divests):
        quantity_to_divest = line.product_uom_qty
        for divest, qty_in_div, location_id in used_divests:
            info.append(self._info_print('----'))
            info.append(self._info_print('Take ' + str(int(qty_in_div)) + ' bt ' +
                                         ' from <a href="#" data-oe-model="x_tw_portefeuille" data-oe-id="' +
                                         str(divest.x_studio_tw_desinvestissement_portefeuille.id) + '">' +
                                         divest.x_studio_tw_desinvestissement_portefeuille.x_name + '</a>'))
            if qty_in_div < divest.x_studio_tw_desinvestissement_quantite_col:

                # duplicate the divest and use only the quantity we need
                dup = divest.copy()
                info.append(self._info_print('Create a duplicated divestment '
                                             '<a href="#" data-oe-model="x_desinvestissement" data-oe-id="' +
                                             str(divest.id) + '">' +
                                             divest.x_name + '</a> <span class="fa fa-long-arrow-right"></span> ' +
                                             '<a href="#" data-oe-model="x_desinvestissement" data-oe-id="' +
                                             str(dup.id) + '">' +
                                             dup.x_name + '</a>'))
                current_divest = dup
                # adjust quantities
                dup.x_studio_tw_desinvestissement_quantite_col = qty_in_div
                divest.x_studio_tw_desinvestissement_quantite_col -= qty_in_div
            else:
                info.append(self._info_print('Use divestment '
                                             '<a href="#" data-oe-model="x_desinvestissement" data-oe-id="' +
                                             str(divest.id) + '">' +
                                             divest.x_name + '</a>'))

                current_divest = divest

            current_divest.x_studio_tw_desinvestissement_date_vente = date.today()
            current_divest.x_studio_tw_desinvestissement_type_vente = 'b2c'
            current_divest.x_studio_sale_order = self.id
            current_divest.x_studio_tw_desinvestissement_prix_vente_unitaire = line.price_unit
            current_divest.x_studio_field_FXKRw = 'todo'

            # the quantity has been set up exactly on the divestment object
            quantity_to_divest -= current_divest.x_studio_tw_desinvestissement_quantite_col

            pf = current_divest.x_studio_tw_desinvestissement_portefeuille

            info.append(self._info_print('<ul>'
                                         '<li>Quantité (cols): ' +
                                         str(current_divest.x_studio_tw_desinvestissement_quantite_col) +
                                         '</li>'
                                         '<li>Date vente: ' + str(current_divest.x_studio_tw_desinvestissement_date_vente) + '</li>'
                                         '<li>Type vente: ' + str(current_divest.x_studio_tw_desinvestissement_type_vente) + '</li>'
                                         '<li>Prix vente unitaire: ' + str(current_divest.x_studio_tw_desinvestissement_prix_vente_unitaire) + '</li>'
                                         '</ul>', False))

            # TODO: we create a PO for each line of each portfolios
            # this is suboptimal when a retail order has several lines, all of
            # them taken from the same portfolio
            self.env['portfolio_extras.pf_action'].divest(pf)
            self._confirm_purchase_order(info, current_divest)

            # re-crate if needed
            self._recrate_if_needed(info, line, current_divest, location_id)

            if quantity_to_divest <= 0:
                break

    def send_mail(self, info):
        self.message_post(body='\n'.join(info),
                          message_type='comment',
                          author_id=3)

    def action_auto_divest(self):
        self.ensure_one()

        info = []
        POVE = self.env['product.product'].search([('default_code', '=', 'POVE')])[0]
        TAXES = self.env['product.product'].search([('default_code', '=', 'TAXES')])[0]

        if self.x_studio_tw_commande_vente_type_commande != 'app':
            _logger.warning('Attempt to auto-divest a non app order')
            return False

        self._read_locations()

        for line in self.order_line:
            product = line.product_id
            if product and product.id != POVE.id and product.id != TAXES.id:
                remaining = line.product_uom_qty
                eqv75_per_col = line.product_id.attribute_value_ids[0].x_studio_tw_valeur_equivalent75_par_col

                used_divests = []
                info.append(self._info_print('<hr>', False))
                info.append(self._info_print('Product: <a href="#" data-oe-model="product.product" data-oe-id="' +
                                             str(product.id) + '">' +
                                             product.display_name + '</a>'))
                # look for the exact mise and quantity
                divests = self.env['x_desinvestissement'].search([
                    ('x_studio_tw_desinvestissement_type', 'in', ['b2c', 'b2b']),
                    ('x_studio_field_FXKRw', '=', 'validated'),
                    ('x_studio_tw_desinvestissement_article', '=', product.id)
                ], order='x_studio_tw_desinvestissement_date asc')
                if divests:
                    remaining, used_divests = self._fill_used_divests(info, line.product_uom_qty, divests)

                if remaining:
                    # look for quantity and volume
                    divests = self.env['x_desinvestissement'].search([
                        ('x_studio_tw_desinvestissement_type', 'in', ['b2c', 'b2b']),
                        ('x_studio_field_FXKRw', '=', 'validated'),
                        ('x_studio_tw_desinvestissement_article.product_tmpl_id', '=', product.product_tmpl_id.id),
                    ], order='x_studio_tw_desinvestissement_date asc')
                    kept = [d for d in divests if eqv75_per_col == d.x_studio_tw_desinvestissement_article.attribute_value_ids[0].x_studio_tw_valeur_equivalent75_par_col]
                    remaining, ud = self._fill_used_divests(info, remaining, kept)
                    used_divests += ud

                if remaining:
                    # check if U'Wine has enough:
                    quants = self.env['stock.quant'].search([
                        ('product_id.product_tmpl_id', '=', product.product_tmpl_id.id),
                        ('owner_id', '=', False),
                        ('location_id', 'in', [self.D2V, self.D2VSD, self.UWV]),
                        ('quantity', '>', 0)
                    ])
                    uw_stock = sum([q.quantity for q in quants if q.product_id.attribute_value_ids[0].x_studio_tw_valeur_equivalent75_par_col == eqv75_per_col])
                    if uw_stock >= remaining:
                        info.append(self._info_print('Take %d %s directly in U\'Wine stock (no divestment needed)' % (remaining, product.name)))
                    else:
                        # TODO: warn more explicitely?
                        info.append(self._info_print('[ERROR] Can\'t find %d %s to divest' % (line.product_uom_qty, product.name)))
                else:
                    self._perform_divests(info, line, used_divests)
        self.send_mail(info)
        return True

    @api.multi
    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()

        for order in self:
            if not order.analytic_account_id:
                raise UserError('Veuillez saisir un compte analytique')
            if not order.fiscal_position_id:
                raise UserError('Veuillez saisir une position fiscale')

        for order in self:
            if res and order.x_studio_tw_commande_vente_type_commande in ['app', 'app_primeur']:
                salesman = order.partner_id.user_id.partner_id
                if not salesman or salesman.id == 3:
                    salesman = order.partner_id.parent_id.user_id.partner_id
                if salesman and salesman.id and salesman.id != 3:
                    order.message_post(subject='Commande app confirmée [' + order.name + ']',
                                       body='Nouvelle commande appli de ' + order.partner_id.display_name,
                                       message_type='notification',
                                       notif_layout='mail.mail_notification_light',
                                       partner_ids=[salesman.id],
                                       author_id=3)
        return res

    @api.depends('state', 'order_line.qty_delivered', 'order_line.product_uom_qty')
    def _all_delivered(self):

        for order in self:
            if order.state in ['draft', 'sent', 'cancel']:
                order.all_delivered = False
                continue
            all_delivered = True
            for ol in order.order_line:
                if ol.qty_delivered < ol.product_uom_qty:
                    if ol.product_id.type == 'product':
                        all_delivered = False
                        break
            order.all_delivered = all_delivered

    @api.multi
    def action_invoice_create(self, grouped=False, final=False):

        # group by portfolio before creating the invoices
        pfs = set([so.x_studio_tw_commande_vente_portefeuille.id for so in self])

        invoice_ids = []
        for pf in pfs:
            invoice_ids.extend(super(SaleOrder, self.filtered(lambda so: so.x_studio_tw_commande_vente_portefeuille.id == pf)).action_invoice_create(grouped, final))
        return invoice_ids


# very heavily inspired by AccountInvoiceSend (in 'account')
class SaleOrderSend(models.TransientModel):
    _name = 'portfolio_extras.sale.order.send'
    _inherits = {'mail.compose.message': 'composer_id'}
    _description = 'Sale Order Send'

    order_ids = fields.Many2many('sale.order', string='Sale orders')
    order_without_email = fields.Text(compute='_compute_order_without_email', string='sale order(s) that will not be sent')
    composer_id = fields.Many2one('mail.compose.message', string='Composer', required=True, ondelete='cascade')
    template_id = fields.Many2one(
        'mail.template', 'Use template', index=True,
        domain="[('model', '=', 'sale.order')]"
    )

    @api.model
    def default_get(self, fields):
        res = super(SaleOrderSend, self).default_get(fields)
        res_ids = self._context.get('active_ids')
        composer = self.env['mail.compose.message'].create({
            'composition_mode': 'comment' if len(res_ids) == 1 else 'mass_mail',
        })
        res.update({
            'order_ids': res_ids,
            'composer_id': composer.id,
        })
        return res

    @api.multi
    @api.onchange('order_ids')
    def _compute_composition_mode(self):
        for wizard in self:
            wizard.composition_mode = 'comment' if len(wizard.order_ids) == 1 else 'mass_mail'

    @api.onchange('template_id')
    def onchange_template_id(self):
        if self.composer_id:
            self.composer_id.template_id = self.template_id.id
            self.composer_id.onchange_template_id_wrapper()

    def _compute_order_without_email(self):
        for wizard in self:
            if len(wizard.order_ids) > 1:
                orders = self.env['sale.order'].search([
                    ('id', 'in', self.env.context.get('active_ids')),
                    ('partner_id.email', '=', False)
                ])
                if orders:
                    wizard.order_without_email = "%s\n%s" % (
                        "The following sale order(s) will not be sent by email, "
                        "because the customers don't have email address.",
                        "\n".join([o.name for o in orders])
                    )
                else:
                    wizard.order_without_email = False

    @api.multi
    def _send_email(self):
        self.composer_id.send_mail()

    @api.multi
    def send_action(self):
        self.ensure_one()
        # Send the mails in the correct language by splitting the ids per lang.
        # This should ideally be fixed in mail_compose_message, so when a fix is made there this whole commit should be reverted.
        # basically self.body (which could be manually edited) extracts self.template_id,
        # which is then not translated for each customer.
        if self.composition_mode == 'mass_mail' and self.template_id:
            active_ids = self.env.context.get('active_ids', self.res_id)
            active_records = self.env[self.model].browse(active_ids)
            langs = active_records.mapped('partner_id.lang')
            default_lang = self.env.context.get('lang', 'en_US')
            for lang in (set(langs) or [default_lang]):
                active_ids_lang = active_records.filtered(lambda r: r.partner_id.lang == lang).ids
                self_lang = self.with_context(active_ids=active_ids_lang, lang=lang)
                self_lang.onchange_template_id()
                self_lang._send_email()
        else:
            self._send_email()
        return {'type': 'ir.actions.act_window_close'}
