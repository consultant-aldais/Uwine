import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    @api.model
    def account_for_pf(self, pf):
        if not pf:
            return None

        for_year = pf.x_studio_tw_portefeuille_annee
        if for_year:
            key = None
            if pf.x_studio_tw_portefeuille_type == 'primeur':
                key = 'PR%d V%d' % (for_year - 2000, for_year - 2001)
            elif pf.x_studio_tw_portefeuille_type == 'opportunity':
                key = 'OP%d' % (for_year - 2000)
            elif pf.x_studio_tw_portefeuille_type == 'allocation':
                key = 'AL%d' % (for_year - 2000)
            elif pf.x_studio_tw_portefeuille_type == 'storage' and \
                    pf.x_studio_tw_portefeuille_mandat.x_studio_tw_mandat_type_mandat == 'mandate':
                key = 'CLP%d' % (for_year - 2000)

            if key:
                accounts = self.env['account.analytic.account'].search([('name', '=', key)], limit=1)
                if accounts:
                    return accounts[0].id
        return None
