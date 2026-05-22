import csv
import os
from datetime import datetime
from app.database import get_connection


def export_quotes_csv(filepath, status_filter=None):
    conn = get_connection()
    cursor = conn.cursor()

    if status_filter:
        cursor.execute('''
            SELECT q.id, q.created_at, q.name, pr.name AS printer_name,
                   f.name AS filament_name, q.filament_used_grams,
                   q.print_time_minutes, q.total_cost, q.suggested_price,
                   q.sale_price, q.status
            FROM quotes q
            LEFT JOIN printers pr ON q.printer_id = pr.id
            LEFT JOIN filaments f ON q.filament_id = f.id
            WHERE q.status = ?
            ORDER BY q.created_at DESC
        ''', (status_filter,))
    else:
        cursor.execute('''
            SELECT q.id, q.created_at, q.name, pr.name AS printer_name,
                   f.name AS filament_name, q.filament_used_grams,
                   q.print_time_minutes, q.total_cost, q.suggested_price,
                   q.sale_price, q.status
            FROM quotes q
            LEFT JOIN printers pr ON q.printer_id = pr.id
            LEFT JOIN filaments f ON q.filament_id = f.id
            ORDER BY q.created_at DESC
        ''')

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return False, 'Nenhum orçamento para exportar.'

    headers = ['ID', 'Data', 'Nome', 'Impressora', 'Filamento',
               'Peso (g)', 'Tempo (min)', 'Custo Total', 'Preço Sugerido',
               'Preço Venda', 'Status']

    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(headers)
        for row in rows:
            writer.writerow([
                row['id'],
                row['created_at'][:10] if row['created_at'] else '',
                row['name'],
                row['printer_name'] or '',
                row['filament_name'] or '',
                f'{row["filament_used_grams"]:.1f}' if row['filament_used_grams'] else '0',
                str(row['print_time_minutes'] or '0'),
                f'{row["total_cost"]:.2f}' if row['total_cost'] else '0',
                f'{row["suggested_price"]:.2f}' if row['suggested_price'] else '0',
                f'{row["sale_price"]:.2f}' if row['sale_price'] else '0',
                (row['status'] or 'orçamento').capitalize(),
            ])

    return True, f'{len(rows)} orçamento(s) exportados para:\n{filepath}'


def export_quote_detail_csv(filepath, qid):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT q.*, pr.name AS printer_name, pr.model AS printer_model,
               f.name AS filament_name, f.material AS filament_material
        FROM quotes q
        LEFT JOIN printers pr ON q.printer_id = pr.id
        LEFT JOIN filaments f ON q.filament_id = f.id
        WHERE q.id = ?
    ''', (qid,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return False, 'Orçamento não encontrado.'

    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['Campo', 'Valor'])
        writer.writerow(['ID', row['id']])
        writer.writerow(['Nome', row['name']])
        writer.writerow(['Data', row['created_at'][:10] if row['created_at'] else ''])
        writer.writerow(['Impressora', row['printer_name'] or ''])
        writer.writerow(['Modelo', row['printer_model'] or ''])
        writer.writerow(['Filamento', row['filament_name'] or ''])
        writer.writerow(['Material', row['filament_material'] or ''])
        writer.writerow(['Peso (g)', f'{row["filament_used_grams"]:.1f}' if row['filament_used_grams'] else ''])
        writer.writerow(['Tempo (min)', str(row['print_time_minutes'] or '')])
        writer.writerow(['Custo Filamento', f'{row["filament_cost"]:.2f}'])
        writer.writerow(['Custo Energia', f'{row["energy_cost"]:.2f}'])
        writer.writerow(['Depreciação', f'{row["depreciation_cost"]:.2f}'])
        writer.writerow(['Manutenção', f'{row["maintenance_cost"]:.2f}'])
        writer.writerow(['Custo Total', f'{row["total_cost"]:.2f}'])
        writer.writerow(['Preço Sugerido', f'{row["suggested_price"]:.2f}'])
        writer.writerow(['Preço Venda', f'{row["sale_price"]:.2f}' if row['sale_price'] else ''])
        writer.writerow(['Margem (%)', f'{row["profit_margin"]:.0f}' if row['profit_margin'] else ''])
        writer.writerow(['Status', (row['status'] or 'orçamento').capitalize()])
        writer.writerow(['Arquivo G-Code', row['gcode_file'] or ''])

    return True, f'Detalhes exportados para:\n{filepath}'
