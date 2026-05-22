from app.database import get_connection

class Filament:
    def __init__(self, id=None, name='', brand='', material='', color='', diameter=1.75, density=1.24, price_per_kg=0):
        self.id = id
        self.name = name
        self.brand = brand
        self.material = material
        self.color = color
        self.diameter = diameter
        self.density = density
        self.price_per_kg = price_per_kg

    def save(self):
        conn = get_connection()
        cursor = conn.cursor()
        if self.id:
            cursor.execute('''
                UPDATE filaments SET name=?, brand=?, material=?, color=?, diameter=?, density=?, price_per_kg=?
                WHERE id=?
            ''', (self.name, self.brand, self.material, self.color, self.diameter, self.density, self.price_per_kg, self.id))
        else:
            cursor.execute('''
                INSERT INTO filaments (name, brand, material, color, diameter, density, price_per_kg)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (self.name, self.brand, self.material, self.color, self.diameter, self.density, self.price_per_kg))
            self.id = cursor.lastrowid
        conn.commit()
        conn.close()

    def delete(self):
        if self.id:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM filaments WHERE id = ?', (self.id,))
            conn.commit()
            conn.close()

    @staticmethod
    def get_all():
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM filaments ORDER BY name')
        rows = cursor.fetchall()
        conn.close()
        filaments = []
        for row in rows:
            f = Filament(
                id=row['id'],
                name=row['name'],
                brand=row['brand'],
                material=row['material'],
                color=row['color'],
                diameter=row['diameter'],
                density=row['density'],
                price_per_kg=row['price_per_kg']
            )
            filaments.append(f)
        return filaments

    @staticmethod
    def get_by_id(id):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM filaments WHERE id = ?', (id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return Filament(
                id=row['id'],
                name=row['name'],
                brand=row['brand'],
                material=row['material'],
                color=row['color'],
                diameter=row['diameter'],
                density=row['density'],
                price_per_kg=row['price_per_kg']
            )
        return None

    def calculate_cost(self, grams):
        return (grams / 1000) * self.price_per_kg