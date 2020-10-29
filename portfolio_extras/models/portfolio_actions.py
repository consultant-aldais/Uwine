import calendar
import logging
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from odoo import models, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

VAT_RATE = 1.20


def _to_date(d):
    if isinstance(d, datetime):
        return d.date()
    return d


def _fee_cap(value, qty, storage, management, cap_rate):
    if storage + management > value * qty * cap_rate:
        return (storage + management) - value * qty * cap_rate


def _year_ratio(d1, d2):
    days_in_year = 365
    if calendar.isleap(d1.year):
        days_in_year = 366

    return min(1.0, max(0.0, (_to_date(d2) - _to_date(d1)).days / days_in_year))


class PortfolioAction(models.TransientModel):
    _name = 'portfolio_extras.pf_action'
    _description = 'Fake model to store portfolio actions'

    @api.model
    def divest(self, pf):
        order_lines = {}
        conso_order_lines = {}
        conso_reverse_order_lines = {}
        conso_sale_order = None

        # list of wines that need to clear customs
        customs_clearances = []

        if pf.x_studio_tw_portefeuille_statut != 'gestion':
            raise UserError('Un désinvestissement ne peut être effectué que sur un portefeuille en statut "gestion"')

        pf_start_date, default_storage_entry_date = self._pf_dates(pf)
        owner_id = pf.x_studio_tw_portefeuille_proprietaire.id

        div_parameters = self.env['x_desinvestissement_parametre_commissions'].search([])[0]
        fee_cap = div_parameters.x_studio_tw_parametre_fee_cap
        yearly_threshold_rate = div_parameters.x_studio_tw_parametre_commission_seuil_plus_value
        overperf_comm_rate = div_parameters.x_studio_tw_parametre_commission_com_plus_value
        margin_sharing_rate = div_parameters.x_studio_tw_parametre_commission_partage_marge
        cellar_management_comm_rate = div_parameters.x_studio_tw_parametre_commission_fixe_reprise_cave

        if pf.x_studio_tw_portefeuille_mandat.x_studio_tw_mandat_marge_revente_cm:
            cellar_management_comm_rate = pf.x_studio_tw_portefeuille_mandat.x_studio_tw_mandat_marge_revente_cm

        analytic_account_id = self.env['account.analytic.account'].account_for_pf(pf)

        D2R = self.env.ref('__import__.stock_location_d2r').id
        D2RSD = self.env.ref('__import__.stock_location_d2rsd').id
        D2V = self.env.ref('__import__.stock_location_d2v').id
        D2VSD = self.env.ref('__import__.stock_location_d2vsd').id
        UW = self.env.ref('__import__.stock_location_uw').id
        UWV = self.env.ref('__import__.stock_location_uwv').id
        PFGV = self.env.ref('__import__.stock_location_pfgv').id
        WPLUS = self.env.ref('__import__.stock_location_wplus').id

        # the contact used for wine+ sale
        JIUJIA = self.env.ref('__import__.res_partner_jiujia').id

        # need customs clearance for sending wines in EU
        europe = self.env.ref('base.europe')

        vat = 1
        if not pf.x_studio_tw_portefeuille_client_investisseur.country_id or \
                pf.x_studio_tw_portefeuille_client_investisseur.country_id.id in europe.country_ids.ids:
            vat = VAT_RATE

        order = None
        for div in pf.x_studio_tw_portefeuille_desinvestissement:
            if div.x_studio_field_FXKRw == 'todo':

                product = div.x_studio_tw_desinvestissement_article
                product_template = product.product_tmpl_id
                qty_bt = div.x_studio_tw_desinvestissement_quantite_col
                sale_date = div.x_studio_tw_desinvestissement_date_vente

                if not div.x_studio_tw_desinvestissement_date_vente:
                    raise UserError(div.x_name + ': Date de vente non renseignée')

                if div.x_studio_tw_desinvestissement_type != 'consommation':
                    if not div.x_studio_tw_desinvestissement_type_vente:
                        raise UserError(div.x_name + ': Type de vente non renseigné')

                    if pf.x_studio_tw_portefeuille_type == 'storage':
                        raise UserError(div.x_name + ': Impossible de désinvestir pour vente sur un Cellar Storage')

                    for mise in pf.x_studio_tw_portefeuille_ligne_mise:

                        b2c = div.x_studio_tw_desinvestissement_type == 'b2c'
                        if b2c and not div.x_studio_sale_order:
                            raise UserError(div.x_name + ': Impossible de désinvestir pour vente B2C sans renseigner '
                                                         'la vente retail correspondante')

                        if mise.x_studio_tw_portefeuille_ligne_mise_mise == product.attribute_value_ids[0] \
                                and mise.x_studio_tw_portefeuille_ligne_mise_modele_article == product_template:

                            entry_price = mise.x_studio_tw_portefeuille_ligne_mise_prix
                            unit_price = div.x_studio_tw_desinvestissement_prix_vente_unitaire
                            duration_year = (sale_date - pf_start_date).days / 365
                            ratio = mise.x_studio_tw_portefeuille_ligne_mise_mise.x_studio_tw_valeur_equivalent75_par_col or 1

                            div['x_studio_tw_desinvestissement_entry'] = entry_price

                            # - in B2C mode, we use the PP to calculate the
                            #   overperformance / margin_sharing / fixed cellar_management commissions
                            # - in B2B mode, we use the actual selling price
                            unit_val = div.x_studio_tw_desinvestissement_prix_vente_unitaire
                            if b2c:
                                pp, med, high = self.env['portfolio_extras.valuation'].get_uw_valuations(
                                    product_template.id, at_date=sale_date)
                                if not pp:
                                    raise UserError('Aucun prix de place pour ' + product_template.name)
                                # PP is place - 8% (low), per eq75
                                unit_val = pp * ratio
                                div['x_studio_tw_desinvestissement_prix_place_unitaire'] = pp * ratio

                            comm = 0
                            comm_overperf = 0
                            comm_sharing = 0
                            comm_fix = 0

                            _logger.info('Divesting %d %s', qty_bt, product_template.name)
                            if pf.x_studio_tw_portefeuille_type in ['primeur', 'allocation', 'opportunity']:
                                # Over-performance
                                if duration_year > 0:
                                    deduction = duration_year * (yearly_threshold_rate / 100) * entry_price

                                    overperf = (unit_val - entry_price - deduction) * qty_bt
                                    if overperf > 0:
                                        comm_overperf = overperf * overperf_comm_rate / 100
                                        comm += comm_overperf

                                    # FYI
                                    div['x_studio_tw_desinvestissement_margin'] = (unit_val - entry_price) / entry_price
                                    div['x_studio_tw_desinvestissement_yearly_margin'] = div['x_studio_tw_desinvestissement_margin'] / duration_year

                            elif pf.x_studio_tw_portefeuille_type_mandat == 'cellar_management':
                                # fix comm. if cellar_management
                                comm_fix = cellar_management_comm_rate / 100 * unit_val * qty_bt
                                comm += comm_fix

                            # if B2C, margin sharing comm. (B2C - PP - sales fee) / 2
                            sales_fee = 0
                            if b2c:
                                diff_bt = div.x_studio_tw_desinvestissement_prix_vente_unitaire - \
                                          div.x_studio_tw_desinvestissement_prix_place_unitaire
                                if diff_bt > 0:
                                    pvb = (div.x_studio_tw_desinvestissement_prix_vente_unitaire -
                                           entry_price) / entry_price
                                    pvba = pvb / duration_year

                                    if pf.x_studio_tw_portefeuille_type_mandat == 'cellar_management' or pvba >= 0.07:
                                        if diff_bt > 20:
                                            sales_fee = 10
                                        elif diff_bt > 10:
                                            sales_fee = diff_bt / 2

                                    comm_sharing = (diff_bt - sales_fee) * margin_sharing_rate / 100 * qty_bt

                                div['x_studio_tw_desinvestissement_fdv_unitaire'] = sales_fee

                                comm += comm_sharing
                                comm += sales_fee * qty_bt

                            wplus_sale = b2c and div.x_studio_sale_order.partner_id.id == JIUJIA
                            locations = [D2V, D2VSD, UWV, PFGV]
                            if wplus_sale:
                                locations = [WPLUS]
                            stock_location = self._get_product_location(product.id,
                                                                        qty_bt,
                                                                        owner_id,
                                                                        locations)
                            if not stock_location:
                                location_names = ', '.join([l.display_name for l in self.env['stock.location'].browse(locations)])
                                raise UserError(div.x_name + ': Article ' + str(product.name) +
                                                ' non disponible'
                                                ' (ou quantité insuffisante: ' +
                                                str(qty_bt) +
                                                ' col(s))'
                                                ' dans le(s) emplacement(s) de désinvestissement pour vente(' +
                                                location_names + ')')

                            order_lines.setdefault(stock_location, []).append((0, 0, {
                                'product_id': product.id,
                                'name': product.name,
                                'product_uom': product.uom_po_id.id,
                                'product_qty': qty_bt,
                                'date_planned': sale_date,
                                'x_studio_tw_commande_achat_ligne_portefeuille': pf.id,
                                'x_studio_tw_ligne_commande_achat_ligne_com_total': comm,
                                'x_studio_tw_ligne_commande_achat_ligne_pu_sans_com': unit_price,
                                'price_unit': unit_price - (comm / qty_bt),
                                'account_analytic_id': analytic_account_id
                            }))

                            div['x_studio_tw_desinvestissement_commission_sur_perf'] = comm_overperf
                            div['x_studio_tw_desinvestissement_commission_partage'] = comm_sharing
                            div['x_studio_tw_desinvestissement_commission_fixe'] = comm_fix
                            div['x_studio_tw_desinvestissement_commission_frais_vente'] = sales_fee * qty_bt

                            # do the wines need customs clearance?
                            # also check if the final destination is in the EU
                            needs_clearance = b2c and not wplus_sale and stock_location in [D2VSD, PFGV]
                            final_dest = div.x_studio_sale_order.partner_shipping_id.country_id
                            if final_dest and final_dest.id not in europe.country_ids.ids:
                                needs_clearance = False

                            if needs_clearance:
                                customs_clearances.append({
                                    'product': product.id,
                                    'quantity': qty_bt,
                                    'location': stock_location
                                })

                            break

                elif pf.x_studio_tw_portefeuille_type not in ['storage', 'cellar_management']:
                    # conso: U'Wine buys the wine (without taxes) to the customer at the entry price

                    for mise in pf.x_studio_tw_portefeuille_ligne_mise:

                        if mise.x_studio_tw_portefeuille_ligne_mise_mise == product.attribute_value_ids[0] \
                                and mise.x_studio_tw_portefeuille_ligne_mise_modele_article == product_template:

                            entry_price = mise.x_studio_tw_portefeuille_ligne_mise_prix

                            locations = [D2R, D2RSD, UW]
                            stock_location = self._get_product_location(product.id, qty_bt, owner_id, locations)
                            if not stock_location:
                                location_names = ', '.join([l.display_name for l in self.env['stock.location'].browse(locations)])
                                raise UserError(div.x_name + ': Article ' + str(product.name) +
                                                ' non disponible'
                                                ' (ou quantité insuffisante: ' +
                                                str(qty_bt) +
                                                ' col(s))'
                                                ' dans le(s) emplacement(s) de désinvestissement pour consommation (' +
                                                location_names + ')')

                            conso_order_lines.setdefault(stock_location, []).append((0, 0, {
                                'product_id': product.id,
                                'name': product.name,
                                'product_uom': product.uom_po_id.id,
                                'product_qty': qty_bt,
                                'date_planned': sale_date,
                                'x_studio_tw_commande_achat_ligne_portefeuille': pf.id,
                                'x_studio_tw_ligne_commande_achat_ligne_com_total': 0,
                                'x_studio_tw_ligne_commande_achat_ligne_pu_sans_com': entry_price,
                                'price_unit': entry_price,
                                'account_analytic_id': analytic_account_id
                            }))

                            needs_clearance = stock_location == D2RSD
                            final_dest = pf.x_studio_tw_portefeuille_client_investisseur.country_id
                            if final_dest and final_dest.id not in europe.country_ids.ids:
                                needs_clearance = False

                            warehouse = self.env['stock.warehouse'].search([
                                ('view_location_id', 'parent_of', stock_location)
                            ], limit=1)[0]

                            conso_reverse_order_lines.setdefault(warehouse, []).append((0, 0, {
                                'product_id': product.id,
                                'name': product.name,
                                'product_uom': product.uom_po_id.id,
                                'product_uom_qty': qty_bt,
                                'price_unit': entry_price,
                            }))

                            # add bottling fees for en-primeur, because we need to invoice the VAT
                            if pf.x_studio_tw_portefeuille_type == 'primeur':
                                mname = mise.x_studio_tw_portefeuille_ligne_mise_mise.name
                                mprod = self.env['product.product'].search([('default_code', '=', mname)])
                                if not mprod:
                                    raise UserError('Article de frais de mise inexistant pour ' + mname)
                                mprice = mprod.lst_price
                                if mprice > 0:
                                    conso_order_lines[stock_location].append((0, 0, {
                                        'product_id': mprod.id,
                                        'name': 'Mise ' + mname + ' pour ' + product.name,
                                        'product_uom': mprod.uom_po_id.id,
                                        'product_qty': qty_bt,
                                        'date_planned': sale_date,
                                        'x_studio_tw_commande_achat_ligne_portefeuille': pf.id,
                                        'x_studio_tw_ligne_commande_achat_ligne_com_total': 0,
                                        'x_studio_tw_ligne_commande_achat_ligne_pu_sans_com': mprice,
                                        'price_unit': mprice,
                                        'account_analytic_id': analytic_account_id
                                    }))
                                    conso_reverse_order_lines[warehouse].append((0, 0, {
                                        'product_id': mprod.id,
                                        'name': product.name,
                                        'product_uom': mprod.uom_po_id.id,
                                        'product_uom_qty': qty_bt,
                                        'price_unit': mprice
                                    }))

                            if needs_clearance:
                                customs_clearances.append({
                                    'product': product.id,
                                    'quantity': qty_bt,
                                    'location': stock_location
                                })
                            break

        for location, lines in order_lines.items():
            picking_type = self.env['stock.picking.type'].search([
                ('default_location_dest_id', '=', location),
                ('code', '=', 'incoming')
            ], limit=1)

            if not picking_type:
                raise UserError('Type d\'opération inconnu: réception vers %s' %
                              self.env['stock.location'].browse(location).display_name)

            picking_type = picking_type[0]

            order = self.env['purchase.order'].create({
                'partner_id': pf.x_studio_tw_portefeuille_client_investisseur.id,
                'x_studio_tw_commande_type_commande': 'desinvest',
                'x_studio_tw_commande_achat_portefeuille': pf.id,
                'state': 'draft',
                'picking_type_id': picking_type.id,
                'x_studio_tw_achat_sousmis_consignation': True,
                'order_line': lines
            })

            for line in order.order_line:
                for div in pf.x_studio_tw_portefeuille_desinvestissement:
                    if div.x_studio_field_FXKRw == 'todo' \
                            and not div.x_studio_tw_desinvestissement_ligne_commande_achat \
                            and line.product_id.id == div.x_studio_tw_desinvestissement_article.id \
                            and line.product_qty == div.x_studio_tw_desinvestissement_quantite_col:
                        div['x_studio_tw_desinvestissement_ligne_commande_achat'] = line

        # Create and validate a purchase order for conso divestments
        for location, lines in conso_order_lines.items():
            picking_type = self.env['stock.picking.type'].search([
                ('default_location_dest_id', '=', location),
                ('code', '=', 'incoming')
            ], limit=1)

            if not picking_type:
                raise UserError('Type d\'opération inconnu: réception vers %s' %
                                self.env['stock.location'].browse(location).display_name)

            picking_type = picking_type[0]

            order = self.env['purchase.order'].create({
                'partner_id': pf.x_studio_tw_portefeuille_client_investisseur.id,
                'x_studio_tw_commande_type_commande': 'desinvest_conso',
                'x_studio_tw_commande_achat_portefeuille': pf.id,
                'state': 'draft',
                'picking_type_id': picking_type.id,
                'x_studio_tw_achat_sousmis_consignation': True,
                'order_line': lines
            })

            # auto-confirm the purchase order (nothing to manually check here)
            order.button_confirm()
            for p in order.picking_ids:
                for m in p.move_lines:
                    for ml in m.move_line_ids:
                        ml.qty_done = ml.product_uom_qty
                p.button_validate()

            for line in order.order_line:
                for div in pf.x_studio_tw_portefeuille_desinvestissement:
                    if div.x_studio_field_FXKRw == 'todo' \
                            and not div.x_studio_tw_desinvestissement_ligne_commande_achat \
                            and line.product_id.id == div.x_studio_tw_desinvestissement_article.id \
                            and line.product_qty == div.x_studio_tw_desinvestissement_quantite_col:
                        div['x_studio_tw_desinvestissement_ligne_commande_achat'] = line

        # create the customs clearance
        if customs_clearances:
            self.env['stock_extras.customs_clearance'].create(customs_clearances)

        # for conso divestments: resell the wines to the customer
        for warehouse, lines in conso_reverse_order_lines.items():
            pove = self.env['product.product'].search([('default_code', '=', 'POVE')])[0]

            # add a line for transport; the price will have to be set manually
            lines.append((0, 0, {
                'product_id': pove.id,
                'name': 'Transport',
                'product_uom': 1,
                'product_uom_qty': 1,
                'price_unit': 1,
            }))

            conso_sale_order = self.env['sale.order'].create({
                'partner_id': pf.x_studio_tw_portefeuille_client_investisseur.id,
                'x_studio_tw_commande_vente_type_commande': 'desinvest_conso',
                'x_studio_tw_commande_vente_portefeuille': pf.id,
                'state': 'draft',
                'warehouse_id': warehouse.id,
                'order_line': lines
            })

            # force the fiscal position to match that of the customer, and not use the 'Mandat (0%)' default one
            conso_sale_order.onchange_partner_shipping_id()
            conso_sale_order._compute_tax_id()

        management_fee_rate = pf.x_studio_tw_portefeuille_mandat.x_studio_tw_mandat_taux_frais_gestion / 100
        if pf.x_studio_tw_portefeuille_type == 'storage':
            management_fee_rate = 0

        fdg_product = self.env['product.product'].search([('default_code', '=', 'FDG')])[0]
        fds_product = self.env['product.product'].search([('default_code', '=', 'FDS')])[0]
        cap_product = self.env['product.product'].search([('default_code', '=', 'CAPF')])[0]

        total_management_fee = 0
        total_storage_fee = 0
        total_cap_fee = 0
        fee_order_lines = []

        for div in pf.x_studio_tw_portefeuille_desinvestissement:

            date_sale = _to_date(div.x_studio_tw_desinvestissement_date_vente)

            if div.x_studio_field_FXKRw == 'todo' and div.x_studio_tw_desinvestissement_date_vente:
                div_mise = div.x_studio_tw_desinvestissement_article.attribute_value_ids[0]
                div_qty = div.x_studio_tw_desinvestissement_quantite_col
                product_template = div.x_studio_tw_desinvestissement_article.product_tmpl_id
                ratio = div_mise.x_studio_tw_valeur_equivalent75_par_col or 1

                if div_qty <= 0:
                    raise UserError('Impossible de traiter un désinvestissement de ' + str(div_qty) + ' cols')

                # Check if we alreay invoiced fees for the year of the sale
                already_invoiced = False
                for f in pf.x_studio_tw_portefeuille_provision:
                    if f.x_studio_tw_portefeuille_provision_annee_type == 'reel' \
                            and f.x_studio_tw_portefeuille_provision_annee_soldee \
                            and f.x_studio_tw_portefeuille_provision_annee_annee == date_sale.year:

                        # Odd case: we already invoiced all the fees for the year
                        # we should probably reimburse the customer...
                        already_invoiced = True
                        break
                if already_invoiced:
                    continue

                for mise in pf.x_studio_tw_portefeuille_ligne_mise:

                    if mise.x_studio_tw_portefeuille_ligne_mise_mise == div_mise and \
                            mise.x_studio_tw_portefeuille_ligne_mise_modele_article == product_template:

                        entry_val = mise.x_studio_tw_portefeuille_ligne_mise_prix
                        stock_entry = self._stock_entry_date(pf, product_template)
                        if not stock_entry:
                            stock_entry = default_storage_entry_date

                        if entry_val <= 0:
                            raise UserError('Impossible de traiter un désinvestissement '
                                            'avec une valeur nulle à l\'entrée: ' + product_template.name)

                        fee_quantities = self._fee_quantities(pf_start_date,
                                                              stock_entry,
                                                              date_sale,
                                                              date_sale.year,
                                                              entry_val,
                                                              div_qty,
                                                              ratio)

                        management_fee = fee_quantities['management'] * management_fee_rate
                        storage_fee = fee_quantities['storage'] * fds_product.lst_price

                        total_management_fee += management_fee
                        total_storage_fee += storage_fee

                        # Don't invoice transportation fees here:
                        # - we won't transport wines to and from PFG anymore
                        # - divested wines are generally not sold the same year as their PFG transportation

                        if management_fee > 0:
                            fee_order_lines.append((0, 0, {
                                'product_id': fdg_product.id,
                                'name': 'Solde des frais de gestion sur ' + product_template.name,
                                'product_uom_qty': management_fee_rate / vat,
                                'x_studio_tw_commande_vente_ligne_portefeuille': pf.id,
                                'x_studio_tw_commande_vente_ligne_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                                'price_unit': fee_quantities['management']
                            }))
                            fee_order_lines.append((0, 0, {
                                'display_type': 'line_note',
                                'name': 'Management fee for %s: %.1f%% x %.2f year x %d cols x %.2f € %s= %.2f € (%.2f € tax incl.)'
                                        % (product_template.name,
                                           100 * management_fee_rate,
                                           fee_quantities['management'] / (entry_val * div_qty or 1),
                                           div_qty,
                                           entry_val,
                                           '/ 1.20 (vat) ' if vat > 1 else '',
                                           fee_quantities['management'] * management_fee_rate / vat,
                                           fee_quantities['management'] * management_fee_rate)
                            }))
                        if storage_fee > 0:
                            fee_order_lines.append((0, 0, {
                                'product_id': fds_product.id,
                                'name': 'Solde des frais de stockage sur ' + product_template.name,
                                'product_uom_qty': fee_quantities['storage'],
                                'x_studio_tw_commande_vente_ligne_portefeuille': pf.id,
                                'x_studio_tw_commande_vente_ligne_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                                'price_unit': fds_product.lst_price / vat
                            }))
                            fee_order_lines.append((0, 0, {
                                'display_type': 'line_note',
                                'name': 'Storage fee for %s: %.2f year x %d eq75 x %.2f € %s= %.2f € (%.2f € tax incl.)' % (
                                    product_template.name,
                                    fee_quantities['storage'] / (div_qty * ratio),
                                    div_qty * ratio,
                                    fds_product.lst_price,
                                    '/ 1.20 (vat) ' if vat > 1 else '',
                                    fee_quantities['storage'] * fds_product.lst_price / vat,
                                    fee_quantities['storage'] * fds_product.lst_price,)
                            }))

                        if pf.x_studio_tw_portefeuille_type in ['primeur', 'opportunity', 'allocation']:
                            cappable_fees = management_fee + storage_fee
                            year_duration = _year_ratio(date(date_sale.year, 1, 1), date_sale)
                            cap = max(0, cappable_fees - fee_cap * div_qty * entry_val * year_duration)
                            if cap > 0:
                                fee_order_lines.append((0, 0, {
                                    'product_id': cap_product.id,
                                    'name': 'Remise (limite frais annuels) sur ' + product_template.name,
                                    'product_uom_qty': -1,
                                    'x_studio_tw_commande_vente_ligne_portefeuille': pf.id,
                                    'x_studio_tw_commande_vente_ligne_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                                    'price_unit': cap / vat
                                }))
                                total_cap_fee += cap
                                fee_order_lines.append((0, 0, {
                                    'display_type': 'line_note',
                                    'name': 'Apply 4%% (%.2f €) cap on fees: -%.2f € (tax incl.) / -%.2f (tax excl.)' %
                                            (fee_cap * div_qty * entry_val * year_duration, cap, cap / vat)
                                }))

                        break

        fee_order = None
        if fee_order_lines:
            fee_order = self._make_fee_order(pf, date.today().year,
                                             fee_order_lines,
                                             total_management_fee,
                                             total_storage_fee,
                                             0,
                                             total_cap_fee,
                                             False,
                                             conso_sale_order)

        for div in pf.x_studio_tw_portefeuille_desinvestissement:
            if div.x_studio_field_FXKRw == 'todo':
                if fee_order is not None:
                    div['x_studio_tw_desinvestissement_ligne_cloture_frais'] = fee_order
                div['x_studio_field_FXKRw'] = 'done'

        return order

    @api.model
    def yearly_fee(self, pf):

        fee_year = datetime.today().year - 1
        end_fee_year = date(fee_year + 1, 1, 1)

        if pf.x_studio_tw_portefeuille_statut not in ['commande_misee', 'gestion']:
            _logger.info('Not invoicing year %d of %s: status not ready', fee_year, pf.x_name)
            return

        for f in pf.x_studio_tw_portefeuille_provision:
            if f.x_studio_tw_portefeuille_provision_annee_type == 'reel' \
                    and f.x_studio_tw_portefeuille_provision_annee_soldee \
                    and f.x_studio_tw_portefeuille_provision_annee_annee == fee_year:
                # Fee already invoiced
                _logger.info('Already invoiced year %d for %s', fee_year, pf.x_name)
                return

        management_fee_rate = pf.x_studio_tw_portefeuille_mandat.x_studio_tw_mandat_taux_frais_gestion / 100
        if pf.x_studio_tw_portefeuille_type == 'storage':
            management_fee_rate = 0

        europe = self.env.ref('base.europe')

        vat = 1
        if not pf.x_studio_tw_portefeuille_client_investisseur.country_id or \
                pf.x_studio_tw_portefeuille_client_investisseur.country_id.id in europe.country_ids.ids:
            vat = VAT_RATE

        cap_product = self.env['product.product'].search([('default_code', '=', 'CAPF')])[0]
        fdt_product = self.env['product.product'].search([('default_code', '=', 'FDT')])[0]
        fdg_product = self.env['product.product'].search([('default_code', '=', 'FDG')])[0]
        fds_product = self.env['product.product'].search([('default_code', '=', 'FDS')])[0]
        if pf.x_studio_tw_portefeuille_type == 'storage':
            fds_product = self.env['product.product'].search([('default_code', '=', 'FDSCS')])[0]

        total_management_qty = 0
        total_storage_qty = 0
        total_transportation_qty = 0
        total_entry_val = 0
        fee_order_lines = [(0, 0, {
            'display_type': 'line_section',
            'name': 'Details'
        })]
        fee_cap = 0.01 * self.env['x_desinvestissement_parametre_commissions'].search([])[0].x_studio_tw_parametre_fee_cap

        if not pf.x_studio_tw_portefeuille_ligne_mise:
            raise UserError('Aucune mise trouvée pour le portefeuille ' + pf.x_name)

        # quick check:
        a = sum([a.x_studio_tw_portefeuille_ligne_affect_quantite_eq75 for a in pf.x_studio_tw_portefeuille_ligne_affect])
        m = sum([m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75 for m in pf.x_studio_tw_portefeuille_ligne_mise])
        if abs(a - m) > 1:
            raise UserError('Erreur dans la saisie des mises sur le portefeuille ' + pf.x_name)

        pf_start_date, default_storage_entry_date = self._pf_dates(pf)

        for m in pf.x_studio_tw_portefeuille_ligne_mise:

            product_template = m.x_studio_tw_portefeuille_ligne_mise_modele_article
            mise = m.x_studio_tw_portefeuille_ligne_mise_mise
            stock_entry = self._stock_entry_date(pf, product_template)
            entry_val = m.x_studio_tw_portefeuille_ligne_mise_prix
            qty = m.x_studio_tw_portefeuille_ligne_mise_quantite_col
            ratio = mise.x_studio_tw_valeur_equivalent75_par_col or 1
            if pf.x_studio_tw_portefeuille_type == 'storage':
                ratio = 1   # in CS pfs, storage is invoiced per col

            if stock_entry:
                # remove all bt divested before end_fee_year
                # quantities divested after end_fee_year still need to be invoiced,
                # because their last fee invoice only covered the year of their divestment
                qty -= sum([d.x_studio_tw_desinvestissement_quantite_col
                            for d in pf.x_studio_tw_portefeuille_desinvestissement
                            if d.x_studio_field_FXKRw == 'done'
                            and d.x_studio_tw_desinvestissement_article.product_tmpl_id == product_template
                            and d.x_studio_tw_desinvestissement_article.attribute_value_ids[0] == mise
                            and d.x_studio_tw_desinvestissement_date_vente < end_fee_year])

            if qty < 0:
                raise UserError('Erreur de quantité sur ' + product_template.name + ' pour ' + pf.x_name)

            if entry_val <= 0:
                raise UserError('Erreur de valorisation de ' + product_template.name)

            if qty > 0:
                # Regarding qty:
                # - if we received some of the wine, assume all bottles have been received
                # - if we did not receive anything, stock_entry is None, or after end_fee_year,
                #   and we will only get management_fees
                fee_quantities = self._fee_quantities(pf_start_date,
                                                      stock_entry,
                                                      end_fee_year,
                                                      fee_year,
                                                      entry_val,
                                                      qty,
                                                      ratio)

                total_management_qty += fee_quantities['management']
                total_storage_qty += fee_quantities['storage']
                total_entry_val += qty * entry_val

                fee_order_lines.append((0, 0, {
                    'display_type': 'line_note',
                    'name': 'Management fee for %s: %.1f%% x %.2f year x %d cols x %.2f € %s= %.2f € (%.2f € tax incl.)' % (
                                product_template.name,
                                100 * management_fee_rate,
                                fee_quantities['management'] / (entry_val * qty or 1),
                                qty,
                                entry_val,
                                '/ 1.20 (vat) ' if vat > 1 else '',
                                fee_quantities['management'] * management_fee_rate / vat,
                                fee_quantities['management'] * management_fee_rate)
                }))
                fee_order_lines.append((0, 0, {
                    'display_type': 'line_note',
                    'name': 'Storage fee for %s: %.2f year x %d eq75 x %.2f € %s= %.2f € (%.2f € tax incl.)' % (
                                product_template.name,
                                fee_quantities['storage'] / (qty * ratio),
                                qty * ratio,
                                fds_product.lst_price,
                                '/ 1.20 (vat) ' if vat > 1 else '',
                                fee_quantities['storage'] * fds_product.lst_price / vat,
                                fee_quantities['storage'] * fds_product.lst_price)
                }))
            # Transportation (in eqv 75), assuming everything was moved
            if m.x_studio_tw_portefeuille_ligne_mise_aller_port:
                total_transportation_qty += m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75
                m['x_studio_tw_portefeuille_ligne_mise_aller_port'] = False
                fee_order_lines.append((0, 0, {
                    'display_type': 'line_note',
                    'name': 'Transport (to PFG) for %s: %d eq75 x %.2f € %s= %.2f € (%.2f € tax incl.)' % (
                        product_template.name,
                        m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75,
                        fdt_product.lst_price,
                        '/ 1.20 (vat) ' if vat > 1 else '',
                        m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75 * fdt_product.lst_price / vat,
                        m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75 * fdt_product.lst_price
                    )
                }))
            if m.x_studio_tw_portefeuille_ligne_mise_retour_port:
                total_transportation_qty += m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75
                m['x_studio_tw_portefeuille_ligne_mise_retour_port'] = False
                fee_order_lines.append((0, 0, {
                    'display_type': 'line_note',
                    'name': 'Transport (from PFG) for %s: %d eq75 x %.2f € %s= %.2f € (%.2f € tax incl.)' % (
                        product_template.name,
                        m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75,
                        fdt_product.lst_price,
                        '/ 1.20 (vat) ' if vat > 1 else '',
                        m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75 * fdt_product.lst_price / vat,
                        m.x_studio_tw_portefeuille_ligne_mise_quantite_eq75 * fdt_product.lst_price)
                }))
        if total_transportation_qty > 0:
            fee_order_lines.insert(0, (0, 0, {
                'product_id': fdt_product.id,
                'name': 'Frais de transport ' + str(fee_year),
                'product_uom_qty': total_transportation_qty,
                'x_studio_tw_commande_vente_ligne_portefeuille': pf.id,
                'x_studio_tw_commande_vente_ligne_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                'price_unit': fdt_product.lst_price / vat
            }))
        if total_storage_qty > 0:
            fee_order_lines.insert(0, (0, 0, {
                'product_id': fds_product.id,
                'name': 'Frais de stockage ' + str(fee_year),
                'product_uom_qty': total_storage_qty,
                'x_studio_tw_commande_vente_ligne_portefeuille': pf.id,
                'x_studio_tw_commande_vente_ligne_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                'price_unit': fds_product.lst_price / vat
            }))
        if total_management_qty > 0:
            fee_order_lines.insert(0, (0, 0, {
                'product_id': fdg_product.id,
                'name': 'Frais de gestion ' + str(fee_year),
                'product_uom_qty': management_fee_rate / vat,
                'x_studio_tw_commande_vente_ligne_portefeuille': pf.id,
                'x_studio_tw_commande_vente_ligne_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                'price_unit': total_management_qty
            }))

        cap = 0
        if pf.x_studio_tw_portefeuille_type in ['primeur', 'opportunity', 'allocation']:
            # ttc
            cappable_fees = management_fee_rate * total_management_qty + \
                            total_storage_qty * fds_product.lst_price + \
                            total_transportation_qty * fdt_product.lst_price

            # first year may be partial
            year_duration = 1
            if pf_start_date > date(fee_year, 1, 1):
                year_duration = _year_ratio(pf_start_date, end_fee_year)

            cap = max(0, cappable_fees - fee_cap * total_entry_val * year_duration)
            if cap > 0:
                fee_order_lines.append((0, 0, {
                    'product_id': cap_product.id,
                    'name': 'Remise (frais annuels limités à ' + format(fee_cap * 100, '.1f') + '%)',
                    'product_uom_qty': -1,
                    'x_studio_tw_commande_vente_ligne_portefeuille': pf.id,
                    'x_studio_tw_commande_vente_ligne_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                    'price_unit': cap / vat
                }))
                fee_order_lines.append((0, 0, {
                    'display_type': 'line_note',
                    'name': 'Apply 4%% (%.2f €) cap on fees: -%.2f € (tax incl.) / -%.2f (tax excl.)' %
                            (fee_cap * total_entry_val * year_duration, cap, cap / vat)
                }))

        return self._make_fee_order(pf, fee_year, fee_order_lines, management_fee_rate * total_management_qty,
                                    total_storage_qty * fds_product.lst_price,
                                    total_transportation_qty * fdt_product.lst_price, cap, True)

    def _make_fee_order(self, pf, for_year, fee_order_lines, fdg, fds, fdt, cap, full_year=False, existing_order=None):
        if fee_order_lines:
            if existing_order:
                existing_order.write({
                    'order_line': fee_order_lines
                })
                fee_order = existing_order
            else:
                fee_order = self.env['sale.order'].create({
                    'partner_id': pf.x_studio_tw_portefeuille_client_investisseur.id,
                    'x_studio_tw_commande_vente_type_commande': 'frais' if full_year else 'cloture',
                    'x_studio_tw_commande_vente_portefeuille': pf.id,
                    'x_studio_tw_commande_vente_mandat': pf.x_studio_tw_portefeuille_mandat.id,
                    'state': 'draft',
                    'order_line': fee_order_lines,
                })
                # force recalculating the fiscal position and make sure taxes are correct
                fee_order.onchange_partner_shipping_id()
                fee_order._compute_tax_id()


            taxes = sum([l.price_tax for l in fee_order.order_line if l.product_id.categ_id.id == 3])

            self.env['x_portefeuille_provision_annee'].create({
                'x_studio_tw_portefeuille_provision_annee_annee': for_year,
                'x_studio_tw_portefeuille_provision_annee_type': 'reel',
                'x_studio_tw_portefeuille_provision_annee_fdg': fdg,
                'x_studio_tw_portefeuille_provision_annee_fds': fds,
                'x_studio_tw_portefeuille_provision_annee_fdt': fdt,
                'x_studio_tw_portefeuille_provision_annee_tva': taxes,
                'x_studio_tw_portefeuille_provision_annee_cap': cap,
                'x_studio_tw_portefeuille_provision_annee_portefeuille': pf.id,
                'x_studio_tw_portefeuille_provision_annee_soldee': full_year
            })
            return fee_order
        return None

    def _stock_entry_date(self, pf, product_template):
        if pf.x_studio_tw_portefeuille_date_entree_stock:
            return pf.x_studio_tw_portefeuille_date_entree_stock

        move = self.env['stock.move'].search([
            ('x_studio_tw_stock_move_portefeuille', '=', pf.id),
            ('state', '=', 'done'),
            ('product_id.product_tmpl_id', '=', product_template.id)
        ], limit=1, order='date asc')
        if move:
            return move[0].date

        return None

    @staticmethod
    def _pf_dates(pf):
        if pf.x_studio_tw_portefeuille_type in ['allocation', 'opportunity']:
            if not pf.x_studio_commande_mise:
                raise UserError('Pas de commande de vin misee sur le portefeuille ' + pf.x_name)
            pf_start_date = _to_date(pf.x_studio_commande_mise.date_order)
            default_storage_entry_date = pf_start_date
        elif pf.x_studio_tw_portefeuille_type == 'primeur':
            if not pf.x_studio_tw_portefeuille_pro_forma:
                if pf.x_studio_tw_portefeuille_annee <= date.today().year - 2:
                    pf_start_date = date(pf.x_studio_tw_portefeuille_annee, 9, 1)
                else:
                    raise UserError('Pas de facture pro forma sur le portefeuille ' + pf.x_name)
            else:
                pf_start_date = _to_date(pf.x_studio_tw_portefeuille_pro_forma.date_order)
            default_storage_entry_date = pf_start_date + relativedelta(months=18)
        else:   # 'storage', 'cellar_management':
            pf_start_date = _to_date(pf.x_studio_tw_portefeuille_mandat.x_studio_tw_mandat_date_signature)
            default_storage_entry_date = pf_start_date

        return pf_start_date, default_storage_entry_date

    def _fee_quantities(self, pf_date, stock_entry, date_end, for_year, value, qty, ratio):
        # pf_date: start of portfolio (for management fees)
        # stock_entry: date of entry in stock
        # date_end: calculate fee up to this date (divestment date for divestments, 31/12 for yearly fees)
        # for_year: year of fees for yearly fees, year of divestment for divestments
        # value: price per bt (buying price by customer)
        # qty: number of bt
        # client: owner of the pf; changes applied VAT
        pf_date = _to_date(pf_date)
        stock_entry = _to_date(stock_entry)
        date_end = _to_date(date_end)

        # We calculate the fees from 01/01/YEAR in every case
        date_start = date(for_year, 1, 1)

        storage_duration = 0
        if stock_entry and stock_entry < date_end:
            storage_duration = _year_ratio(max(date_start, stock_entry), date_end)

        management_duration = 0
        if pf_date and pf_date < date_end:
            # cut management fees after five years (on the 5th anniversary)
            max_management_date = pf_date + relativedelta(years=5)
            mg_end_date = min(date_end, max_management_date)
            management_duration = _year_ratio(max(date_start, pf_date), mg_end_date)

        return {
            'storage': storage_duration * qty * ratio,
            'management': management_duration * qty * value
        }

    def _get_product_location(self, product_id, qty, owner_id, locations):

        # only take quants with a large enough quantity: we will not deal with divestments of bottles from
        # different locations
        stocks = self.env['stock.quant'].search([
            ('product_id', '=', product_id),
            ('owner_id', '=', owner_id),
            ('location_id', 'in', locations),
            ('quantity', '>=', qty)
        ])
        results = {s.location_id.id: s.quantity for s in stocks}
        # take the 1st available location in the list of locations
        for l in locations:
            if l in results:
                return l
        return None


