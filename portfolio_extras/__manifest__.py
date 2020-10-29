# -*- coding: utf-8 -*-
{
    'name': "Portfolios extras",

    'summary': """Extra tools for portfolios & mandates""",

    'description': """
        Extra tools for portfolios & mandates
    """,

    'author': "U'Wine",
    'website': "http://www.uwine.fr",
    'version': '1.3',

    'depends': [
        'base',
        'sale',
        'analytic',
        'stock_extras',
        'account_reports',
        'mail',
        'web_studio'  # this app depends on 'studio_customization'
    ],
    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'data/report_paperformat_landscape.xml',
        'data/ir_cron_data.xml',
        'views/portfolio_report.xml',
        'views/valuation.xml',
        'views/pf_stock_report.xml',
        'views/sale_views.xml',
    ],
    'installable': True
}
