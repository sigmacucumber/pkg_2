import sys
import os
import csv
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog, QTableWidgetItem, QHeaderView, QMessageBox
from PyQt5.QtCore import QThread, pyqtSignal
from PIL import Image

from mainwindow_ui import Ui_MainWindow


class ImageScanner(QThread):
    progress = pyqtSignal(int, int)
    result = pyqtSignal(list)
    status = pyqtSignal(str)

    def __init__(self, files, formats):
        super().__init__()
        self.files = files
        self.formats = formats
        self._stop = False

    def run(self):
        results = []
        total = len(self.files)
        for idx, filepath in enumerate(self.files):
            if self._stop:
                break
            ext = os.path.splitext(filepath)[1].upper().lstrip('.')
            if ext not in self.formats:
                continue
            try:
                info = self.read_image_info(filepath)
                if info:
                    results.append(info)
            except Exception:
                results.append({
                    'name': os.path.basename(filepath),
                    'size': 'Ошибка', 'dpi': '-', 'depth': '-',
                    'compression': '-', 'filesize': '-',
                })
            self.progress.emit(idx + 1, total)
            self.status.emit(f'Обработано {idx + 1} из {total}')
        self.result.emit(results)

    def read_image_info(self, filepath):
        try:
            with Image.open(filepath) as img:
                w, h = img.size
                mode = img.mode
                depth_map = {'1': 1, 'L': 8, 'P': 8, 'RGB': 24, 'RGBA': 32,
                             'CMYK': 32, 'YCbCr': 24, 'LAB': 24, 'HSV': 24, 'I': 32, 'F': 32}
                depth = depth_map.get(mode, '?')
                
                # DPI
                dpi = img.info.get('dpi', None)
                if dpi and dpi[0] > 0 and dpi[1] > 0:
                    dpi_str = f'{int(dpi[0])}x{int(dpi[1])}'
                else:
                    dpi_str = '-'
                
                # Сжатие
                compression = img.info.get('compression', None)
                fmt = img.format
                
                if compression is None:
                    # Нет информации о сжатии — определяем по формату
                    if fmt == 'JPEG': compression = 'JPEG'
                    elif fmt == 'PNG': compression = 'PNG (deflate)'
                    elif fmt == 'GIF': compression = 'LZW'
                    elif fmt == 'BMP': compression = 'Нет'
                    elif fmt == 'TIFF': compression = 'Нет'
                    elif fmt == 'PCX': compression = 'RLE'
                    else: compression = fmt or '-'
                elif isinstance(compression, int):
                    # Числовое значение (например, 0 для BMP)
                    if compression == 0:
                        compression = 'Нет'
                    elif compression == 1:
                        compression = 'RLE'
                    else:
                        compression = f'Тип {compression}'
                else:
                    compression = str(compression)
                
                filesize_kb = os.path.getsize(filepath) / 1024
                return {
                    'name': os.path.basename(filepath),
                    'size': f'{w}x{h}', 'dpi': dpi_str,
                    'depth': str(depth), 'compression': str(compression),
                    'filesize': f'{filesize_kb:.1f}',
                }
        except Exception:
            return None

    def stop(self):
        self._stop = True


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # Настройка таблицы — задаём заголовки колонок
        headers = ['№', 'Имя файла', 'Размер (px)', 'Разрешение (dpi)',
                   'Глубина цвета', 'Сжатие', 'Размер файла (КБ)']
        self.tableResults.setHorizontalHeaderLabels(headers)
        self.tableResults.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tableResults.setRowCount(0)
        
        # Убираем номера строк слева (они дублируют колонку №)
        self.tableResults.verticalHeader().setVisible(False)

        # Включаем все чекбоксы по умолчанию
        for chk in [self.chkJPG, self.chkGIF, self.chkTIF,
                    self.chkBMP, self.chkPNG, self.chkPCX]:
            chk.setChecked(True)

        self.selected_files = []
        self.selected_folder = None
        self.scanner = None

        # Подключаем кнопки
        self.btnSelectFolder.clicked.connect(self.select_folder)
        self.btnSelectFiles.clicked.connect(self.select_files)
        self.btnScan.clicked.connect(self.start_scan)
        self.btnExport.clicked.connect(self.export_csv)
        self.btnClear.clicked.connect(self.clear_table)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Выберите папку')
        if folder:
            self.selected_folder = folder
            self.selected_files = []
            self.lblStatus.setText(f'Папка: {folder}')

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, 'Выберите файлы', '',
            'Images (*.jpg *.jpeg *.gif *.tif *.tiff *.bmp *.png *.pcx)')
        if files:
            self.selected_files = files
            self.selected_folder = None
            self.lblStatus.setText(f'Выбрано файлов: {len(files)}')

    def get_enabled_formats(self):
        formats = set()
        if self.chkJPG.isChecked(): formats.update({'JPG', 'JPEG'})
        if self.chkGIF.isChecked(): formats.add('GIF')
        if self.chkTIF.isChecked(): formats.update({'TIF', 'TIFF'})
        if self.chkBMP.isChecked(): formats.add('BMP')
        if self.chkPNG.isChecked(): formats.add('PNG')
        if self.chkPCX.isChecked(): formats.add('PCX')
        return formats

    def start_scan(self):
        files = []
        if self.selected_folder:
            formats = self.get_enabled_formats()
            for root, dirs, filenames in os.walk(self.selected_folder):
                for f in filenames:
                    ext = os.path.splitext(f)[1].upper().lstrip('.')
                    if ext in formats:
                        files.append(os.path.join(root, f))
        elif self.selected_files:
            files = self.selected_files
        else:
            QMessageBox.warning(self, 'Внимание', 'Сначала выберите папку или файлы!')
            return
        if not files:
            QMessageBox.warning(self, 'Внимание', 'Файлы не найдены!')
            return
        formats = self.get_enabled_formats()
        self.scanner = ImageScanner(files, formats)
        self.scanner.progress.connect(self.update_progress)
        self.scanner.status.connect(self.lblStatus.setText)
        self.scanner.result.connect(self.display_results)
        self.scanner.start()
        self.btnScan.setEnabled(False)
        self.lblStatus.setText('Сканирование...')

    def update_progress(self, current, total):
        percent = int((current / total) * 100) if total > 0 else 0
        self.progressBar.setValue(percent)

    def display_results(self, results):
        self.tableResults.setRowCount(len(results))
        for row, info in enumerate(results):
            self.tableResults.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.tableResults.setItem(row, 1, QTableWidgetItem(info['name']))
            self.tableResults.setItem(row, 2, QTableWidgetItem(info['size']))
            self.tableResults.setItem(row, 3, QTableWidgetItem(info['dpi']))
            self.tableResults.setItem(row, 4, QTableWidgetItem(info['depth']))
            self.tableResults.setItem(row, 5, QTableWidgetItem(info['compression']))
            self.tableResults.setItem(row, 6, QTableWidgetItem(info['filesize']))
        self.label_2.setText(f'Всего файлов: {len(results)}')
        self.lblStatus.setText('Готово!')
        self.btnScan.setEnabled(True)

    def clear_table(self):
        self.tableResults.setRowCount(0)
        self.progressBar.setValue(0)
        self.lblStatus.setText('Готово')
        self.label_2.setText('lblStats')
        self.selected_files = []
        self.selected_folder = None

    def export_csv(self):
        if self.tableResults.rowCount() == 0:
            QMessageBox.warning(self, 'Внимание', 'Таблица пуста!')
            return
        filepath, _ = QFileDialog.getSaveFileName(
            self, 'Сохранить CSV', '', 'CSV files (*.csv)')
        if filepath:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['№', 'Имя файла', 'Размер (px)', 'Разрешение (dpi)',
                                 'Глубина цвета', 'Сжатие', 'Размер файла (КБ)'])
                for row in range(self.tableResults.rowCount()):
                    row_data = []
                    for col in range(self.tableResults.columnCount()):
                        item = self.tableResults.item(row, col)
                        row_data.append(item.text() if item else '')
                    writer.writerow(row_data)
            QMessageBox.information(self, 'Успех', f'Экспортировано в {filepath}')


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())