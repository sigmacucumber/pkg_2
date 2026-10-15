import sys
import os
import csv
import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog, QTableWidgetItem, QHeaderView, QMessageBox
from PyQt5.QtCore import QThread, pyqtSignal
from mainwindow_ui import Ui_MainWindow

def read_bmp(filepath):
    with open(filepath, 'rb') as f:
        sig = f.read(2)
        if sig != b'BM':
            return None
        f.read(8)
        offset = struct.unpack('<I', f.read(4))[0]
        struct.unpack('<I', f.read(4))[0]
        width = struct.unpack('<i', f.read(4))[0]
        height = struct.unpack('<i', f.read(4))[0]
        planes = struct.unpack('<H', f.read(2))[0]
        bpp = struct.unpack('<H', f.read(2))[0]
        compression = struct.unpack('<I', f.read(4))[0]
        f.read(16)
        ppm_x = struct.unpack('<i', f.read(4))[0]
        ppm_y = struct.unpack('<i', f.read(4))[0]
        dpi_x = round(ppm_x * 0.0254) if ppm_x > 0 else 0
        dpi_y = round(ppm_y * 0.0254) if ppm_y > 0 else 0
        comp_str = {0: 'Нет', 1: 'RLE-8', 2: 'RLE-4', 3: 'Bitfields'}.get(compression, f'Тип {compression}')
        return {
            'size': f'{abs(width)}x{abs(height)}',
            'dpi': f'{dpi_x}x{dpi_y}' if dpi_x > 0 else '-',
            'depth': str(bpp),
            'compression': comp_str,
            'extra': f'Planes: {planes}, Offset: {offset}'
        }

def read_png(filepath):
    with open(filepath, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            return None
        f.read(4)
        chunk_type = f.read(4)
        if chunk_type != b'IHDR':
            return None
        width = struct.unpack('>I', f.read(4))[0]
        height = struct.unpack('>I', f.read(4))[0]
        bit_depth = struct.unpack('B', f.read(1))[0]
        color_type = struct.unpack('B', f.read(1))[0]
        f.read(3)
        color_types = {0: 'Grayscale', 2: 'RGB', 3: 'Palette', 4: 'Grayscale+Alpha', 6: 'RGBA'}
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type, 1)
        total_depth = bit_depth * channels
        dpi_str = '-'
        while True:
            chunk_len_data = f.read(4)
            if len(chunk_len_data) < 4:
                break
            chunk_len = struct.unpack('>I', chunk_len_data)[0]
            chunk_type = f.read(4)
            if chunk_type == b'pHYs':
                phys_data = f.read(chunk_len)
                ppu_x = struct.unpack('>I', phys_data[0:4])[0]
                ppu_y = struct.unpack('>I', phys_data[4:8])[0]
                unit = phys_data[8]
                if unit == 1:
                    dpi_x = round(ppu_x * 0.0254)
                    dpi_y = round(ppu_y * 0.0254)
                    dpi_str = f'{dpi_x}x{dpi_y}'
                break
            else:
                f.read(chunk_len + 4)
        return {
            'size': f'{width}x{height}',
            'dpi': dpi_str,
            'depth': str(total_depth),
            'compression': 'Deflate',
            'extra': f'BitDepth: {bit_depth}, ColorType: {color_types.get(color_type, "?")}'
        }

def read_jpeg(filepath):
    with open(filepath, 'rb') as f:
        if f.read(2) != b'\xff\xd8':
            return None
        width = height = 0
        dpi_str = '-'
        quant_tables = []
        depth = 24
        while True:
            marker = f.read(2)
            if len(marker) < 2:
                break
            if marker[0] != 0xff:
                break
            marker_type = marker[1]
            if marker_type == 0xe0:
                length = struct.unpack('>H', f.read(2))[0]
                data = f.read(length - 2)
                if data[:5] == b'JFIF\x00':
                    units = data[7]
                    x_density = struct.unpack('>H', data[8:10])[0]
                    y_density = struct.unpack('>H', data[10:12])[0]
                    if units == 1:
                        dpi_str = f'{x_density}x{y_density}'
                    elif units == 2:
                        dpi_str = f'{round(x_density*2.54)}x{round(y_density*2.54)}'
            elif marker_type == 0xe1:
                length = struct.unpack('>H', f.read(2))[0]
                f.read(length - 2)
            elif marker_type == 0xdb:
                length = struct.unpack('>H', f.read(2))[0]
                dqt_data = f.read(length - 2)
                if len(dqt_data) >= 65:
                    precision = dqt_data[0] >> 4
                    table = []
                    for i in range(64):
                        idx = 1 + i * (2 if precision else 1)
                        if precision:
                            val = struct.unpack('>H', dqt_data[idx:idx+2])[0]
                        else:
                            val = dqt_data[idx]
                        table.append(val)
                    quant_tables.append(table)
            elif marker_type in (0xc0, 0xc1, 0xc2):
                length = struct.unpack('>H', f.read(2))[0]
                data = f.read(length - 2)
                precision = data[0]
                height = struct.unpack('>H', data[1:3])[0]
                width = struct.unpack('>H', data[3:5])[0]
                components = data[5]
                depth = components * precision
                break
            elif marker_type == 0xda:
                break
            else:
                length = struct.unpack('>H', f.read(2))[0]
                f.read(length - 2)
        if width == 0:
            return None
        quant_str = '-'
        if quant_tables:
            qt = quant_tables[0][:8]
            quant_str = f'[{", ".join(map(str, qt))}, ...]'
        return {
            'size': f'{width}x{height}',
            'dpi': dpi_str,
            'depth': str(depth),
            'compression': 'JPEG',
            'extra': f'QuantTable: {quant_str}'
        }

def read_gif(filepath):
    with open(filepath, 'rb') as f:
        sig = f.read(6)
        if sig not in (b'GIF87a', b'GIF89a'):
            return None
        version = sig.decode('ascii')
        width = struct.unpack('<H', f.read(2))[0]
        height = struct.unpack('<H', f.read(2))[0]
        packed = struct.unpack('B', f.read(1))[0]
        f.read(2)
        has_gct = (packed >> 7) & 1
        gct_size = packed & 7
        num_colors = 2 ** (gct_size + 1) if has_gct else 0
        if has_gct:
            f.read(3 * num_colors)
        return {
            'size': f'{width}x{height}',
            'dpi': '-',
            'depth': '8',
            'compression': 'LZW',
            'extra': f'Version: {version}, Colors: {num_colors}, GCT: {has_gct}'
        }

def read_tiff(filepath):
    with open(filepath, 'rb') as f:
        byte_order = f.read(2)
        if byte_order == b'II':
            endian = '<'
        elif byte_order == b'MM':
            endian = '>'
        else:
            return None
        magic = struct.unpack(endian + 'H', f.read(2))[0]
        if magic != 42:
            return None
        ifd_offset = struct.unpack(endian + 'I', f.read(4))[0]
        f.seek(ifd_offset)
        num_entries = struct.unpack(endian + 'H', f.read(2))[0]
        width = height = 0
        bpp = 0
        compression = 1
        dpi_x = dpi_y = 0
        resolution_unit = 2
        for _ in range(num_entries):
            tag = struct.unpack(endian + 'H', f.read(2))[0]
            type_id = struct.unpack(endian + 'H', f.read(2))[0]
            count = struct.unpack(endian + 'I', f.read(4))[0]
            value_data = f.read(4)
            type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}
            type_size = type_sizes.get(type_id, 1)
            if count * type_size <= 4:
                value = value_data
            else:
                value_offset = struct.unpack(endian + 'I', value_data)[0]
                pos = f.tell()
                f.seek(value_offset)
                value = f.read(count * type_size)
                f.seek(pos)
            if tag == 256:
                if type_id == 3:
                    width = struct.unpack(endian + 'H', value[:2])[0]
                else:
                    width = struct.unpack(endian + 'I', value[:4])[0]
            elif tag == 257:
                if type_id == 3:
                    height = struct.unpack(endian + 'H', value[:2])[0]
                else:
                    height = struct.unpack(endian + 'I', value[:4])[0]
            elif tag == 258:
                if type_id == 3:
                    bpp = struct.unpack(endian + 'H', value[:2])[0]
                else:
                    bpp = struct.unpack(endian + 'I', value[:4])[0]
            elif tag == 259:
                compression = struct.unpack(endian + 'H', value[:2])[0]
            elif tag == 282:
                if type_id == 5:
                    val_offset = struct.unpack(endian + 'I', value_data)[0]
                    pos = f.tell()
                    f.seek(val_offset)
                    num = struct.unpack(endian + 'I', f.read(4))[0]
                    den = struct.unpack(endian + 'I', f.read(4))[0]
                    f.seek(pos)
                    dpi_x = round(num / den) if den > 0 else 0
            elif tag == 283:
                if type_id == 5:
                    val_offset = struct.unpack(endian + 'I', value_data)[0]
                    pos = f.tell()
                    f.seek(val_offset)
                    num = struct.unpack(endian + 'I', f.read(4))[0]
                    den = struct.unpack(endian + 'I', f.read(4))[0]
                    f.seek(pos)
                    dpi_y = round(num / den) if den > 0 else 0
            elif tag == 296:
                resolution_unit = struct.unpack(endian + 'H', value[:2])[0]
        if width == 0 or height == 0:
            return None
        if resolution_unit == 3:
            dpi_x = round(dpi_x * 2.54)
            dpi_y = round(dpi_y * 2.54)
        dpi_str = f'{dpi_x}x{dpi_y}' if dpi_x > 0 else '-'
        comp_str = {1: 'Нет', 2: 'CCITT', 5: 'LZW', 6: 'JPEG', 8: 'Deflate'}.get(compression, f'Тип {compression}')
        return {
            'size': f'{width}x{height}',
            'dpi': dpi_str,
            'depth': str(bpp) if bpp > 0 else '?',
            'compression': comp_str,
            'extra': f'Endian: {"LE" if endian == "<" else "BE"}, Unit: {resolution_unit}'
        }

def read_pcx(filepath):
    with open(filepath, 'rb') as f:
        header = f.read(128)
        if len(header) < 128:
            return None
        version = header[1]
        encoding = header[2]
        bits_per_pixel = header[3]
        xmin = struct.unpack('<H', header[4:6])[0]
        ymin = struct.unpack('<H', header[6:8])[0]
        xmax = struct.unpack('<H', header[8:10])[0]
        ymax = struct.unpack('<H', header[10:12])[0]
        width = xmax - xmin + 1
        height = ymax - ymin + 1
        h_dpi = struct.unpack('<H', header[12:14])[0]
        v_dpi = struct.unpack('<H', header[14:16])[0]
        num_planes = header[65]
        depth = bits_per_pixel * num_planes
        f.seek(-769, 2)
        palette_marker = f.read(1)
        has_256_palette = (palette_marker == b'\x0c')
        if has_256_palette:
            num_colors = 256
        elif num_planes == 1 and bits_per_pixel <= 8:
            num_colors = 2 ** bits_per_pixel
        else:
            num_colors = 2 ** depth
        comp_str = 'RLE' if encoding == 1 else 'Нет'
        return {
            'size': f'{width}x{height}',
            'dpi': f'{h_dpi}x{v_dpi}' if h_dpi > 0 else '-',
            'depth': str(depth),
            'compression': comp_str,
            'extra': f'Version: {version}, Planes: {num_planes}, Colors: {num_colors}'
        }

PARSERS = {
    'BMP': read_bmp,
    'PNG': read_png,
    'JPG': read_jpeg,
    'JPEG': read_jpeg,
    'GIF': read_gif,
    'TIF': read_tiff,
    'TIFF': read_tiff,
    'PCX': read_pcx,
}

class ImageScanner(QThread):
    progress = pyqtSignal(int, int)
    result = pyqtSignal(list)
    status = pyqtSignal(str)

    def __init__(self, files, formats):
        super().__init__()
        self.files = files
        self.formats = formats
        self._stop = False

    def process_file(self, filepath):
        ext = os.path.splitext(filepath)[1].upper().lstrip('.')
        if ext not in self.formats:
            return None
        try:
            parser = PARSERS.get(ext)
            if parser:
                info = parser(filepath)
                if info:
                    info['name'] = os.path.basename(filepath)
                    info['filesize'] = f'{os.path.getsize(filepath) / 1024:.1f}'
                    return info
                else:
                    return {
                        'name': os.path.basename(filepath),
                        'size': 'Ошибка', 'dpi': '-', 'depth': '-',
                        'compression': '-', 'filesize': '-', 'extra': 'Неверный формат'
                    }
            else:
                return {
                    'name': os.path.basename(filepath),
                    'size': '-', 'dpi': '-', 'depth': '-',
                    'compression': '-', 'filesize': '-', 'extra': 'Парсер не найден'
                }
        except Exception as e:
            return {
                'name': os.path.basename(filepath),
                'size': 'Ошибка', 'dpi': '-', 'depth': '-',
                'compression': '-', 'filesize': '-', 'extra': str(e)
            }

    def run(self):
        results = []
        total = len(self.files)
        processed = 0
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_file = {executor.submit(self.process_file, f): f for f in self.files}
            for future in as_completed(future_to_file):
                if self._stop:
                    break
                result = future.result()
                if result:
                    results.append(result)
                processed += 1
                self.progress.emit(processed, total)
                self.status.emit(f'Обработано {processed} из {total}')
        self.result.emit(results)

    def stop(self):
        self._stop = True

class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.tableResults.setColumnCount(8)
        headers = ['№', 'Имя файла', 'Размер (px)', 'Разрешение (dpi)',
                   'Глубина цвета', 'Сжатие', 'Размер (КБ)', 'Доп. информация']
        self.tableResults.setHorizontalHeaderLabels(headers)
        self.tableResults.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.tableResults.setColumnWidth(0, 50)
        for i in range(1, 8):
            self.tableResults.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)
        self.tableResults.setRowCount(0)
        self.tableResults.verticalHeader().setVisible(False)
        for chk in [self.chkJPG, self.chkGIF, self.chkTIF,
                    self.chkBMP, self.chkPNG, self.chkPCX]:
            chk.setChecked(True)
        self.selected_files = []
        self.selected_folder = None
        self.scanner = None
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
            self.tableResults.setItem(row, 7, QTableWidgetItem(info.get('extra', '-')))
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
                                 'Глубина цвета', 'Сжатие', 'Размер (КБ)', 'Доп. информация'])
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
