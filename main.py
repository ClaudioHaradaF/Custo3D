import sys
import os

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from app.database import init_database
from app.ui import Cost3DApp
import tkinter as tk

def main():
    init_database()
    root = tk.Tk()
    app = Cost3DApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()