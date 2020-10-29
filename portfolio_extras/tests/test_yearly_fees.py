from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from odoo.tests import TransactionCase, tagged


CREATE_PORTFOLIOS_ID = 469
CREATE_DOWNPAYMENT_ID = 495
CREATE_FEES_ID = 510

CREATE_PROFORMA = 497
CREATE_COM_MISEE = 478
RUN_DESINVEST = 503

COL_ID = 21
CBO6_75_ID = 17
CBO3_150_ID = 21

VAT_RATE = 1.20
QTY_EQV75 = 48
QTY_CBO6_75 = 24
QTY_CBO1_150 = 12
VALUE_EQ75 = 100

D2_STOCK = 55


@tagged('-standard', 'yearly_fee')
class YearlyFeeTest(TransactionCase):

    def setUp(self):
        super(YearlyFeeTest, self).setUp()

    def _create_user(self):
        self.user = self.env['res.partner'].create({
            'name': 'TEST USER',
            'customer': True,
        })

    def _create_product(self):
        self.tmpl = self.env['product.template'].create({
            'name': 'ANGELUS TEST 2030',
            'type': 'product',
            'default_code': 'VTEST',
            'categ_id': 2,
            'uom_id': COL_ID,
            'uom_po_id': COL_ID,
            'x_studio_tw_template_millesime': '2030',
            'x_studio_tw_template_vin': 5,
            'attribute_line_ids': [[0, 0, {'attribute_id': 1, 'value_ids': [[6, False, [13, CBO6_75_ID, CBO3_150_ID]]]}]]
        })
        self.prod_75 = self.env['product.product'].search([('default_code', '=', 'SGANG230B')])[0]
        self.prod_150 = self.env['product.product'].search([('default_code', '=', 'SGANG230F')])[0]

        prp = self.env['product.pricelist'].search([('name', 'ilike', 'PRP')])[0]

        self.env['product.pricelist.item'].create({
            'pricelist_id': prp.id,
            'applied_on': '1_product',
            'compute_price': 'fixed',
            'product_tmpl_id': self.tmpl.id,
            'fixed_price': 120
        })

        # Force stock quantity
        d2_stock = self.env['stock.location'].browse(D2_STOCK)
        self.env['stock.quant']._update_available_quantity(self.prod_75, d2_stock, 1000)
        self.env['stock.quant']._update_available_quantity(self.prod_150, d2_stock, 1000)

    def _create_mandate_pf(self, d):
        self.md = self.env['x_mandat'].create({
            'x_name': 'MANDAT TEST',
            'x_studio_tw_mandat_statut': 'signed',
            'x_studio_tw_mandat_date_signature': d,
            'x_studio_tw_mandat_type_mandat': 'mandate',
            'x_currency_id': self.env.ref('base.EUR').id,
            'x_studio_tw_mandat_annee_en_cours': d.year,
            'x_studio_tw_mandat_client_investisseur': self.user.id,
            'x_studio_tw_mandat_beneficiaire': '',
            'x_studio_tw_mandat_montant': 5000,
            'x_studio_tw_mandat_nombre_annees': 1,
            'x_studio_tw_mandat_premiere_annee': d.year,
            'x_studio_tw_mandat_cumul_mandats_client': 5000,
            'x_studio_tw_mandat_categorie_engagement': 1,
            'x_studio_tw_mandat_profil_portefeuille': 1,
            'x_studio_tw_mandat_primeur_pourc': 100,
            'x_studio_tw_mandat_allocation_pourc': 0,
            'x_studio_tw_mandat_opportunite_pourc': 0,
            'x_studio_tw_mandat_liquidite': 'liquid',
            'x_studio_tw_mandat_liquidite_plafond': 0,
            'x_studio_tw_mandat_sortie': 'uwine_decision',
            'x_studio_tw_mandat_sortie_plafond': 100
        })

        self.env['x_tw_echeancier_mandat'].create({
            'x_studio_tw_echeancier_mandat_mandat': self.md.id,
            'x_studio_tw_echeancier_mandat_annee': d.year,
            'x_studio_tw_echeancier_mandat_primeur': 5000,
            'x_studio_tw_echeancier_mandat_opportunite': 0,
            'x_studio_tw_echeancier_mandat_allocation': 0,
            'x_studio_tw_echeancier_mandat_clp': 0,
            'x_studio_tw_echeancier_acompte_cree': False
        })

        ctx = {
            'active_model': 'x_mandat',
            'active_ids': self.md.ids,
            'active_id': self.md.id
        }
        self.env['ir.actions.server'].browse(CREATE_PORTFOLIOS_ID).with_context(**ctx).run()
        self.pf = self.md.x_studio_field_fJC0x[0]

        self.env['x_portefeuille_ligne_affectation'].create({
            'x_studio_tw_portefeuille_ligne_aff_portefeuille': self.pf.id,
            'x_studio_tw_portefeuille_ligne_affect_modele_article': self.tmpl.id,
            'x_studio_tw_portefeuille_ligne_affect_quantite_eq75': QTY_EQV75,
            'x_studio_tw_portefeuille_ligne_affect_prix_achat': VALUE_EQ75
        })

        self.env['x_portefeuille_ligne_mise'].create({
            'x_studio_tw_portefeuille_ligne_mise_portefeuille': self.pf.id,
            'x_studio_tw_portefeuille_ligne_mise_modele_article': self.tmpl.id,
            'x_studio_tw_portefeuille_ligne_mise_mise': CBO6_75_ID,
            'x_studio_tw_portefeuille_ligne_mise_quantite_col': QTY_CBO6_75,
            'x_studio_tw_portefeuille_ligne_mise_prix': VALUE_EQ75,
        })
        self.env['x_portefeuille_ligne_mise'].create({
            'x_studio_tw_portefeuille_ligne_mise_portefeuille': self.pf.id,
            'x_studio_tw_portefeuille_ligne_mise_modele_article': self.tmpl.id,
            'x_studio_tw_portefeuille_ligne_mise_mise': CBO3_150_ID,
            'x_studio_tw_portefeuille_ligne_mise_quantite_col': QTY_CBO1_150,
            'x_studio_tw_portefeuille_ligne_mise_prix': 2 * VALUE_EQ75,
        })

        ctx = {
            'active_model': 'x_tw_portefeuille',
            'active_ids': self.pf.ids,
            'active_id': self.pf.id
        }
        self.env['ir.actions.server'].browse(CREATE_PROFORMA).with_context(**ctx).run()

        proforma = self.env['sale.order'].search([
            ('x_studio_tw_commande_vente_portefeuille', '=', self.pf.id),
            ('x_studio_tw_commande_vente_type_commande', '=', 'pro_forma')
        ])[0]
        proforma.write({'date_order': d})

        self.env['ir.actions.server'].browse(CREATE_COM_MISEE).with_context(**ctx).run()
        com = self.env['sale.order'].search([
            ('x_studio_tw_commande_vente_portefeuille', '=', self.pf.id),
            ('x_studio_tw_commande_vente_type_commande', '=', 'vin')
        ])[0]
        com.action_confirm()
        com.write({
            'date_order': d + relativedelta(months=1),
            'confirmation_date': d + relativedelta(months=1)
        })

    def _receive_wines(self, d):
        picking = self.pf.x_studio_commande_mise.picking_ids[0]
        picking.action_assign()
        for ml in picking.move_line_ids:
            ml.write({
                'qty_done': ml.product_uom_qty
            })
        picking.button_validate()
        picking.write({
            'date_done': d,
            'scheduled_date': d,
            'date': d
        })
        picking.move_lines.write({
            'date': d,
            'date_expected': d,
            'create_date': d
        })

    def _invoice_fees(self):
        return self.env['portfolio_extras.pf_action'].yearly_fee(self.pf)

    def test_full_year_fdg(self):
        self._create_user()
        self._create_product()
        self._create_mandate_pf(date(2018, 9, 1))

        fee_order = self._invoice_fees()
        self.assertIsNotNone(fee_order)
        self.assertEqual(fee_order.partner_id, self.user)
        self.assertEqual(fee_order.x_studio_tw_commande_vente_portefeuille, self.pf)
        self.assertEqual(fee_order.x_studio_tw_commande_vente_mandat, self.md)
        self.assertEqual(fee_order.x_studio_tw_commande_vente_type_commande, 'frais')
        self.assertAlmostEqual(fee_order.amount_total, VAT_RATE * 0.02 * QTY_EQV75 * VALUE_EQ75)

    def test_partial_year_fdg(self):
        self._create_user()
        self._create_product()
        self._create_mandate_pf(date(2019, 9, 1))

        fee_order = self._invoice_fees()
        self.assertIsNotNone(fee_order)
        self.assertAlmostEqual(fee_order.amount_total, 0.33 * VAT_RATE * 0.02 * QTY_EQV75 * VALUE_EQ75, 0)

    def test_full_year_fds(self):
        self._create_user()
        self._create_product()
        self._create_mandate_pf(date(2017, 9, 1))
        self._receive_wines(datetime(2018, 7, 1))

        fee_order = self._invoice_fees()
        self.assertIsNotNone(fee_order)
        fdg = VAT_RATE * 0.02 * QTY_EQV75 * VALUE_EQ75
        fds = VAT_RATE * QTY_EQV75
        self.assertAlmostEqual(fee_order.amount_total, fdg + fds, 0)

    def test_partial_year_fds(self):
        self._create_user()
        self._create_product()
        self._create_mandate_pf(date(2017, 9, 1))
        self._receive_wines(datetime(2019, 9, 1))

        fee_order = self._invoice_fees()
        self.assertIsNotNone(fee_order)
        fdg = VAT_RATE * 0.02 * QTY_EQV75 * VALUE_EQ75
        fds = VAT_RATE * 0.33 * QTY_EQV75
        self.assertAlmostEqual(fee_order.amount_total, fdg + fds, 0)

    def test_portfolio_stock_entry_date(self):
        self._create_user()
        self._create_product()
        self._create_mandate_pf(date(2017, 9, 1))
        self._receive_wines(datetime(2019, 9, 1))

        self.pf.write({
            'x_studio_tw_portefeuille_date_entree_stock': date(2017, 9, 1)
        })

        fee_order = self._invoice_fees()
        self.assertIsNotNone(fee_order)
        fdg = VAT_RATE * 0.02 * QTY_EQV75 * VALUE_EQ75
        fds = VAT_RATE * QTY_EQV75
        self.assertAlmostEqual(fee_order.amount_total, fdg + fds, 0)
