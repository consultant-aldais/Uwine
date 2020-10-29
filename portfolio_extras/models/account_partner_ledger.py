from odoo import models, api


class ReportPartnerLedger(models.AbstractModel):
    _inherit = 'account.partner.ledger'

    @api.model
    def _get_lines(self, options, line_id=None):
        lines = super(ReportPartnerLedger, self)._get_lines(options, line_id)

        # list of partner_ids to prefetch
        partner_ids = []
        for l in lines:
            if isinstance(l['id'], str) and l['id'].startswith('partner_'):
                partner_id = int(l['id'].replace('partner_', ''))
                partner_ids.append(partner_id)

        partners = self.env['res.partner'].browse(partner_ids)
        pp = {p['id']: p for p in partners}

        # set a nicer name on each line
        for l in lines:
            if isinstance(l['id'], str) and l['id'].startswith('partner_'):
                partner_id = int(l['id'].replace('partner_', ''))
                p = pp[partner_id]
                l['name'] = '[' + str(partner_id) + '] ' + \
                            ((p.x_studio_tw_contact_prenom + ' ') if p.x_studio_tw_contact_prenom else '') + \
                            p.name

        return lines
