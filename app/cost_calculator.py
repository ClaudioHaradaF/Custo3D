from app.database import get_setting
from app.filament import Filament
from app.printer import Printer

class CostCalculator:
    def __init__(self, printer=None, filament=None):
        self.printer = printer
        self.filament = filament
        raw = get_setting('energy_price')
        try:
            self.energy_price = float(raw) if raw else 0.85
        except (ValueError, TypeError):
            self.energy_price = 0.85

    def calculate(self, filament_used_grams, print_time_minutes):
        costs = {
            'filament_cost': 0,
            'energy_cost': 0,
            'depreciation_cost': 0,
            'maintenance_cost': 0,
            'total_cost': 0
        }

        if self.filament and filament_used_grams:
            costs['filament_cost'] = self.filament.calculate_cost(filament_used_grams)

        if self.printer and print_time_minutes > 0:
            hours = print_time_minutes / 60

            power_kw = self.printer.power_watts / 1000
            costs['energy_cost'] = hours * power_kw * self.energy_price

            depreciation_per_hour = self.printer.purchase_price / self.printer.lifespan_hours
            costs['depreciation_cost'] = hours * depreciation_per_hour

            costs['maintenance_cost'] = hours * self.printer.maintenance_cost_per_hour

        costs['total_cost'] = (
            costs['filament_cost'] +
            costs['energy_cost'] +
            costs['depreciation_cost'] +
            costs['maintenance_cost']
        )

        return costs

    @staticmethod
    def calculate_manual(filament_price_per_kg, filament_grams, printer_power_watts, print_minutes,
                        printer_price, printer_lifespan_hours, maintenance_per_hour, energy_price_per_kwh):
        filament_cost = (filament_grams / 1000) * filament_price_per_kg

        hours = print_minutes / 60
        power_kw = printer_power_watts / 1000
        energy_cost = hours * power_kw * energy_price_per_kwh

        depreciation_per_hour = printer_price / printer_lifespan_hours
        depreciation_cost = hours * depreciation_per_hour

        maintenance_cost = hours * maintenance_per_hour

        total = filament_cost + energy_cost + depreciation_cost + maintenance_cost

        return {
            'filament_cost': round(filament_cost, 2),
            'energy_cost': round(energy_cost, 2),
            'depreciation_cost': round(depreciation_cost, 2),
            'maintenance_cost': round(maintenance_cost, 2),
            'total_cost': round(total, 2)
        }