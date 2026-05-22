PRIMARY = '#1976D2'
PRIMARY_LIGHT = '#BBDEFB'
PRIMARY_DARK = '#0D47A1'
SECONDARY = '#F57C00'
BG_LIGHT = '#F0F4F8'
CARD_BG = '#FFFFFF'
TEXT_PRIMARY = '#1A1A2E'
TEXT_SECONDARY = '#6B7280'
SUCCESS = '#16A34A'
WARNING = '#F59E0B'
ERROR = '#DC2626'

BTN_RADIUS = 8
BTN_PADX = 22
BTN_PADY = 6

DARK_BG = '#1E1E1E'
DARK_FG = '#FFFFFF'
DARK_CARD = '#2D2D2D'
DARK_SUBTEXT = '#BBBBBB'

STATUS_TAGS = {
    'concluído': '#DCFCE7',
    'em espera': '#FEF3C7',
    'em andamento': '#DBEAFE',
    'cancelado': '#FEE2E2',
}

APP_NAME = 'Cost3D'
APP_VERSION = '2.1'
APP_TITLE = f'{APP_NAME} v{APP_VERSION} - Controle de Custos para Impressão 3D'


def fmt_currency(value):
    if value is None:
        return 'R$ 0,00'
    return f'R$ {value:.2f}'


def fmt_time(minutes):
    if not minutes:
        return '0min'
    h = minutes // 60
    m = minutes % 60
    if h > 0:
        return f'{h}h {m:02d}min'
    return f'{m}min'


def fmt_status(status):
    if not status:
        return 'Orçamento'
    return status.capitalize()
