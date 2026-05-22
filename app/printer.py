from app.database import get_connection

class Printer:
    def __init__(self, id=None, name='', model='', purchase_price=0, power_watts=0, lifespan_hours=10000, maintenance_cost_per_hour=0):
        self.id = id
        self.name = name
        self.model = model
        self.purchase_price = purchase_price
        self.power_watts = power_watts
        self.lifespan_hours = lifespan_hours
        self.maintenance_cost_per_hour = maintenance_cost_per_hour

    def save(self):
        conn = get_connection()
        cursor = conn.cursor()
        if self.id:
            cursor.execute('''
                UPDATE printers SET name=?, model=?, purchase_price=?, power_watts=?, lifespan_hours=?, maintenance_cost_per_hour=?
                WHERE id=?
            ''', (self.name, self.model, self.purchase_price, self.power_watts, self.lifespan_hours, self.maintenance_cost_per_hour, self.id))
        else:
            cursor.execute('''
                INSERT INTO printers (name, model, purchase_price, power_watts, lifespan_hours, maintenance_cost_per_hour)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (self.name, self.model, self.purchase_price, self.power_watts, self.lifespan_hours, self.maintenance_cost_per_hour))
            self.id = cursor.lastrowid
        conn.commit()
        conn.close()

    def delete(self):
        if self.id:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM printers WHERE id = ?', (self.id,))
            conn.commit()
            conn.close()

    @staticmethod
    def get_all():
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM printers ORDER BY name')
        rows = cursor.fetchall()
        conn.close()
        printers = []
        for row in rows:
            p = Printer(
                id=row['id'],
                name=row['name'],
                model=row['model'],
                purchase_price=row['purchase_price'],
                power_watts=row['power_watts'],
                lifespan_hours=row['lifespan_hours'],
                maintenance_cost_per_hour=row['maintenance_cost_per_hour']
            )
            printers.append(p)
        return printers

    @staticmethod
    def get_by_id(id):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM printers WHERE id = ?', (id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return Printer(
                id=row['id'],
                name=row['name'],
                model=row['model'],
                purchase_price=row['purchase_price'],
                power_watts=row['power_watts'],
                lifespan_hours=row['lifespan_hours'],
                maintenance_cost_per_hour=row['maintenance_cost_per_hour']
            )
        return None