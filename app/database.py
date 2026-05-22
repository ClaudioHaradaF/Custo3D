import sqlite3
import os
import sys
from datetime import datetime

if getattr(sys, 'frozen', False):
    DB_PATH = os.path.join(os.path.dirname(sys.executable), 'custo3d.db')
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'custo3d.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_database():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            material TEXT,
            color TEXT,
            diameter REAL DEFAULT 1.75,
            density REAL DEFAULT 1.24,
            price_per_kg REAL NOT NULL,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS printers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            model TEXT,
            manufacturer TEXT,
            purchase_price REAL NOT NULL,
            power_watts REAL NOT NULL,
            lifespan_hours INTEGER DEFAULT 10000,
            maintenance_cost_per_hour REAL DEFAULT 0,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            printer_id INTEGER,
            filament_id INTEGER,
            gcode_file TEXT,
            thumbnail_data BLOB,
            filament_used_grams REAL,
            print_time_minutes INTEGER,
            filament_cost REAL,
            energy_cost REAL,
            depreciation_cost REAL,
            maintenance_cost REAL,
            total_cost REAL,
            suggested_price REAL,
            sale_price REAL,
            profit_margin REAL DEFAULT 30,
            status TEXT DEFAULT 'orçamento',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (printer_id) REFERENCES printers(id),
            FOREIGN KEY (filament_id) REFERENCES filaments(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        INSERT OR IGNORE INTO settings (key, value) VALUES ('energy_price', '0.85')
    ''')
    cursor.execute('''
        INSERT OR IGNORE INTO settings (key, value) VALUES ('profit_margin_default', '30')
    ''')
    cursor.execute('''
        INSERT OR IGNORE INTO settings (key, value) VALUES ('theme', 'light')
    ''')

    # Migrations for existing databases
    try:
        cursor.execute('ALTER TABLE quotes ADD COLUMN sale_price REAL')
    except:
        pass

    conn.commit()
    conn.close()

def get_setting(key):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    result = cursor.fetchone()
    conn.close()
    return result['value'] if result else None

def update_setting(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()