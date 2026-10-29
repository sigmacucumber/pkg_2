import sys
import os
import struct
import zlib
import csv
from PyQt5.QtWidgets import (QApplication, QMainWindow, QFileDialog,
                              QTableWidgetItem, QHeaderView, QMessageBox)
from PyQt5.QtCore import QThread, pyqtSignal, QMutex, QWaitCondition
from PIL import Image

from mainwindow_ui import Ui_MainWindow


class ImageParser:
    SUPPORTED_EXT = {'.png', '.bmp', '.gif', '.jpg', '.jpeg', '.jpe',
                     '.tif', '.tiff', '.pcx'}
    METER_TO_INCH = 0.0254
    _NOT_IMAGE_STAGES = {'empty', 'too_short', 'signature', 'not_image'}
    _UNREADABLE_STAGES = {'stat', 'open', 'io'}
    
    KNOWN_NON_IMAGE = [
        (b'%PDF-', 'PDF-документ'),
        (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'OLE2'),
        (b'PK\x03\x04', 'ZIP-контейнер'),
        (b'PK\x05\x06', 'пустой ZIP-архив'),
        (b'Rar!\x1a\x07\x00', 'RAR-архив v4'),
        (b'Rar!\x1a\x07\x01\x00', 'RAR-архив v5'),
        (b'7z\xbc\xaf\x27\x1c', '7-Zip архив'),
        (b'\x1f\x8b', 'GZIP'),
        (b'BZh', 'BZIP2'),
        (b'\xfd7zXZ\x00', 'XZ'),
        (b'MZ', 'Windows EXE/DLL'),
        (b'\x7fELF', 'Linux ELF-бинарник'),
        (b'SQLite format 3\x00', 'SQLite БД'),
        (b'ID3', 'MP3 (с ID3)'),
        (b'\xff\xfb', 'MP3'),
        (b'fLaC', 'FLAC'),
        (b'OggS', 'OGG'),
        (b'{\\rtf', 'RTF'),
    ]

    DEPTH_MAP = {
        '1': 1, 'L': 8, 'P': 8, 'RGB': 24, 'RGBA': 32,
        'CMYK': 32, 'YCbCr': 24, 'LAB': 24, 'HSV': 24, 'I': 32, 'F': 32,
    }

    @staticmethod
    def _err(info, stage, msg, fmt=None):
        info['error'] = msg
        info['error_stage'] = stage
        if stage in ImageParser._NOT_IMAGE_STAGES:
            info['status'] = 'not_an_image'
        elif stage in ImageParser._UNREADABLE_STAGES:
            info['status'] = 'unreadable'
        else:
            info['status'] = 'broken'
        if fmt is not None:
            info['format'] = fmt
        return info

    @staticmethod
    def _classify_non_image(header, probe, file_size):
        if header.startswith(b'RIFF') and len(header) >= 12 and header[8:12] == b'WEBP':
            return ('WebP', 'изображение WebP (не поддерживается парсером)')
        for magic, name in ImageParser.KNOWN_NON_IMAGE:
            if header.startswith(magic):
                return (name, f"обнаружен {name}")
        for magic, name in ImageParser.KNOWN_NON_IMAGE:
            if magic in probe:
                idx = probe.index(magic)
                return (name, f"обнаружен {name} (сигнатура на смещении {idx})")
        try:
            sample = header.decode('utf-8')
            if sample and all(c.isprintable() or c in '\r\n\t' for c in sample):
                return ('Текст (UTF-8)', 'похоже на текстовый файл')
        except UnicodeDecodeError:
            pass
        if file_size == 0:
            return ('Пустой файл', 'файл не содержит данных')
        return (None, None)

    @staticmethod
    def get_info(filepath):
        info = {
            'name': os.path.basename(filepath),
            'size': 'N/A', 'dpi': 'N/A', 'depth': 'N/A',
            'compression': 'N/A', 'filesize': 'N/A',
            'status': 'ok', 'error': None,
        }
        
        try:
            file_size = os.path.getsize(filepath)
        except OSError as e:
            return ImageParser._err(info, 'stat',
                f"Не удалось получить размер файла: {e.strerror or e}")
        
        if file_size == 0:
            return ImageParser._err(info, 'empty', "Файл пустой (0 байт)",
                                    fmt='Not an image')
        
        if file_size < 14:
            return ImageParser._err(info, 'too_short',
                f"Файл слишком короткий: {file_size} байт (< 14)",
                fmt='Not an image')
        
        try:
            f = open(filepath, 'rb', buffering=0)
        except OSError as e:
            return ImageParser._err(info, 'open',
                f"Не удалось открыть файл: {e.strerror or e}")
        
        try:
            with f:
                header = f.read(16)
                if len(header) < 14:
                    return ImageParser._err(info, 'header',
                        f"Обрезан заголовок: прочитано {len(header)} из 14 байт",
                        fmt='Not an image')
                
                f.seek(0)
                probe = f.read(1024)
                
                is_png = header.startswith(b'\x89PNG\r\n\x1a\n')
                is_bmp = header.startswith(b'BM')
                is_gif = header.startswith(b'GIF87a') or header.startswith(b'GIF89a')
                is_jpg = header.startswith(b'\xff\xd8')
                is_tif = header.startswith(b'II*\x00') or header.startswith(b'MM\x00*')
                is_pcx = (len(header) >= 4 and header[0] == 0x0A and header[2] == 0x01)
                
                if not (is_png or is_bmp or is_gif or is_jpg or is_tif or is_pcx):
                    fmt_name, descr = ImageParser._classify_non_image(
                        header, probe, file_size)
                    if fmt_name:
                        return ImageParser._err(info, 'not_image',
                            f"Файл не является изображением: {descr}",
                            fmt=fmt_name)
                    return ImageParser._err(info, 'signature',
                        f"Формат не распознан (сигнатура: {header[:8].hex(' ')})",
                        fmt='Unknown')
                
                if is_png:
                    return ImageParser._parse_png(f, info, file_size)
                elif is_bmp:
                    return ImageParser._parse_bmp(f, info, file_size)
                elif is_gif:
                    return ImageParser._parse_gif(f, info, file_size)
                elif is_jpg:
                    return ImageParser._parse_jpeg(f, info, file_size)
                elif is_tif:
                    return ImageParser._parse_tiff(f, info)
                elif is_pcx:
                    return ImageParser._parse_pcx_raw(f, info, file_size)
                    
        except OSError as e:
            return ImageParser._err(info, 'io',
                f"Ошибка ввода-вывода: {e.strerror or e}")
        except (struct.error, ValueError, OverflowError, TypeError) as e:
            return ImageParser._err(info, 'parse',
                f"Ошибка разбора: {type(e).__name__}: {e}")
        except Exception as e:
            return ImageParser._err(info, 'unknown',
                f"Неизвестная ошибка: {type(e).__name__}: {e}")
        
        return info

    @staticmethod
    def _parse_png(f, info, file_size):
        info['format'] = 'PNG'
        info['compression'] = 'Lossless (DEFLATE)'
        info['filesize'] = f"{file_size / 1024:.1f}"
        info['dpi'] = "Не задано (72x72 дефолт)"
        
        f.seek(16)
        ihdr = f.read(10)
        if len(ihdr) < 10:
            return ImageParser._err(info, 'IHDR',
                f"PNG: обрезан IHDR (прочитано {len(ihdr)} из 10 байт)")
        
        width = int.from_bytes(ihdr[0:4], 'big')
        height = int.from_bytes(ihdr[4:8], 'big')
        bit_depth = ihdr[8]
        color_type = ihdr[9]
        
        if width == 0 or height == 0:
            return ImageParser._err(info, 'IHDR',
                f"PNG: некорректные размеры {width}x{height}")
        
        valid_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16},
                       3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
        if color_type not in valid_depths:
            return ImageParser._err(info, 'IHDR',
                f"PNG: неизвестный color_type={color_type}")
        if bit_depth not in valid_depths[color_type]:
            return ImageParser._err(info, 'IHDR',
                f"PNG: bit_depth={bit_depth} недопустим для color_type={color_type}")
        
        info['size'] = f'{width}x{height}'
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
        info['depth'] = f"{bit_depth * channels} bit"
        
        saw_iend = False
        saw_idat = False
        f.seek(8)
        
        while True:
            lb = f.read(4)
            if len(lb) < 4:
                break
            length = int.from_bytes(lb, 'big')
            ctype = f.read(4)
            if len(ctype) < 4:
                return ImageParser._err(info, 'chunk',
                    "PNG: обрезан заголовок чанка (нет типа)")
            
            chunk_data = f.read(length)
            if len(chunk_data) < length:
                return ImageParser._err(info, 'chunk',
                    f"PNG: обрезаны данные чанка {ctype!r}")
            
            crc_raw = f.read(4)
            if len(crc_raw) < 4:
                return ImageParser._err(info, 'chunk',
                    f"PNG: обрезан CRC чанка {ctype!r}")
            
            expected_crc = int.from_bytes(crc_raw, 'big')
            actual_crc = zlib.crc32(ctype + chunk_data) & 0xffffffff
            if expected_crc != actual_crc:
                return ImageParser._err(info, 'corrupted',
                    f"PNG: повреждение данных — неверная CRC чанка {ctype!r}")
            
            if ctype == b'pHYs' and length == 9:
                ppmx = int.from_bytes(chunk_data[0:4], 'big')
                ppmy = int.from_bytes(chunk_data[4:8], 'big')
                unit = chunk_data[8]
                if unit == 1 and ppmx > 0 and ppmy > 0:
                    info['dpi'] = (f"{int(ppmx * ImageParser.METER_TO_INCH)}x"
                                   f"{int(ppmy * ImageParser.METER_TO_INCH)}")
                continue
            if ctype == b'IDAT':
                saw_idat = True
                continue
            if ctype == b'IEND':
                saw_iend = True
                break
        
        if not saw_iend:
            return ImageParser._err(info, 'IEND',
                "PNG: файл оборван до чанка IEND (изображение не завершено)")
        if not saw_idat:
            return ImageParser._err(info, 'IDAT',
                "PNG: отсутствуют данные изображения (IDAT)")
        
        return info

    @staticmethod
    def _parse_bmp(f, info, file_size):
        info['format'] = 'BMP'
        info['filesize'] = f"{file_size / 1024:.1f}"
        info['dpi'] = "Не задано (72x72 дефолт)"
        
        f.seek(2)
        declared_size = int.from_bytes(f.read(4), 'little')
        if declared_size != 0 and declared_size != file_size:
            return ImageParser._err(info, 'header',
                f"BMP: несоответствие размера — заголовок заявляет "
                f"{declared_size} байт, реально {file_size}")
        
        f.seek(14)
        dib_raw = f.read(4)
        if len(dib_raw) < 4:
            return ImageParser._err(info, 'DIB',
                f"BMP: обрезан размер DIB (прочитано {len(dib_raw)} из 4)")
        
        dib_size = int.from_bytes(dib_raw, 'little')
        
        if dib_size == 12:
            wh = f.read(8)
            if len(wh) < 8:
                return ImageParser._err(info, 'DIB',
                    f"BMP(CORE): обрезан заголовок")
            width = int.from_bytes(wh[0:2], 'little')
            height = int.from_bytes(wh[2:4], 'little')
            bpp = int.from_bytes(wh[6:8], 'little')
            info['compression'] = 'None'
        elif dib_size >= 40:
            buf = f.read(36)
            if len(buf) < 36:
                return ImageParser._err(info, 'DIB',
                    f"BMP(INFO): обрезан заголовок")
            width = int.from_bytes(buf[0:4], 'little', signed=True)
            height = abs(int.from_bytes(buf[4:8], 'little', signed=True))
            bpp = int.from_bytes(buf[10:12], 'little')
            comp = int.from_bytes(buf[12:16], 'little')
            if comp == 0:
                info['compression'] = 'None'
            elif comp == 1:
                info['compression'] = 'RLE8'
            elif comp == 2:
                info['compression'] = 'RLE4'
            elif comp == 3:
                info['compression'] = 'Bitfields'
            else:
                info['compression'] = f'Compressed ({comp})'
            
            ppmx = int.from_bytes(buf[24:28], 'little', signed=True)
            ppmy = int.from_bytes(buf[28:32], 'little', signed=True)
            if ppmx > 0 and ppmy > 0:
                info['dpi'] = (f"{int(ppmx * ImageParser.METER_TO_INCH)}x"
                               f"{int(ppmy * ImageParser.METER_TO_INCH)}")
        else:
            return ImageParser._err(info, 'DIB',
                f"BMP: неизвестный размер DIB={dib_size}")
        
        if width <= 0 or height == 0:
            return ImageParser._err(info, 'DIB',
                f"BMP: некорректные размеры {width}x{height}")
        
        info['size'] = f'{width}x{height}'
        info['depth'] = f'{bpp} bit'
        return info

    @staticmethod
    def _parse_gif(f, info, file_size):
        info['format'] = 'GIF'
        info['compression'] = 'Lossless (LZW)'
        info['filesize'] = f"{file_size / 1024:.1f}"
        info['dpi'] = "Не задано (72x72 дефолт)"
        
        f.seek(6)
        whp = f.read(5)
        if len(whp) < 5:
            return ImageParser._err(info, 'LSD',
                f"GIF: обрезан Logical Screen Descriptor")
        
        width = int.from_bytes(whp[0:2], 'little')
        height = int.from_bytes(whp[2:4], 'little')
        
        if width == 0 or height == 0:
            return ImageParser._err(info, 'LSD',
                f"GIF: нулевые размеры {width}x{height}")
        
        info['size'] = f'{width}x{height}'
        info['depth'] = f"{(whp[4] & 7) + 1} bit"
        
        tail_size = min(1024, file_size)
        f.seek(-tail_size, 2)
        tail = f.read(tail_size)
        
        if b'\x3b' not in tail:
            return ImageParser._err(info, 'trailer',
                "GIF: отсутствует трейлер 0x3B (файл оборван)")
        
        return info

    @staticmethod
    def _parse_jpeg(f, info, file_size):
        info['format'] = 'JPEG'
        info['compression'] = 'Lossy (JPEG)'
        info['filesize'] = f"{file_size / 1024:.1f}"
        info['dpi'] = "Не задано (72x72 дефолт)"
        
        f.seek(2)
        sof_found = False
        
        while True:
            mb = f.read(2)
            if len(mb) < 2:
                return ImageParser._err(info, 'EOF',
                    "JPEG: файл оборван до маркера SOF (нет размеров)")
            
            if mb[0] != 0xFF:
                return ImageParser._err(info, 'marker',
                    f"JPEG: потеряна синхронизация маркеров "
                    f"(байт 0x{mb[0]:02X} вместо 0xFF)")
            
            marker = int.from_bytes(mb, 'big')
            
            if (0xffc0 <= marker <= 0xffcf and 
                marker not in (0xffc4, 0xffc8, 0xffcc)):
                seg_len_raw = f.read(2)
                if len(seg_len_raw) < 2:
                    return ImageParser._err(info, 'SOF',
                        "JPEG: обрезан SOF (нет длины)")
                chunk = f.read(6)
                if len(chunk) < 6:
                    return ImageParser._err(info, 'SOF',
                        f"JPEG: обрезан SOF")
                
                precision = chunk[0]
                h = int.from_bytes(chunk[1:3], 'big')
                w = int.from_bytes(chunk[3:5], 'big')
                channels = chunk[5]
                
                if w == 0 or h == 0:
                    return ImageParser._err(info, 'SOF',
                        f"JPEG: нулевые размеры {w}x{h}")
                
                info['size'] = f'{w}x{h}'
                info['depth'] = f"{precision * channels} bit"
                sof_found = True
                
                sof_seg_len = int.from_bytes(seg_len_raw, 'big')
                sof_payload_left = sof_seg_len - 2 - 6
                if sof_payload_left > 0:
                    f.seek(sof_payload_left, 1)
                break
            
            elif marker == 0xffe0:
                lb = f.read(2)
                if len(lb) < 2:
                    return ImageParser._err(info, 'APP0',
                        "JPEG: обрезан APP0 (нет длины)")
                length = int.from_bytes(lb, 'big')
                if length < 2:
                    return ImageParser._err(info, 'APP0',
                        f"JPEG: битая длина APP0={length}")
                app0 = f.read(length - 2)
                if len(app0) < length - 2:
                    return ImageParser._err(info, 'APP0',
                        f"JPEG: обрезан APP0")
                
                if app0.startswith(b'JFIF\x00') and len(app0) >= 12:
                    unit = app0[7]
                    xd = int.from_bytes(app0[8:10], 'big')
                    yd = int.from_bytes(app0[10:12], 'big')
                    if unit == 1:
                        info['dpi'] = f"{xd}x{yd}"
                    elif unit == 2:
                        info['dpi'] = f"{int(xd * 2.54)}x{int(yd * 2.54)}"
            
            elif marker in (0xffd8, 0xffd9) or 0xffd0 <= marker <= 0xffd7:
                continue
            else:
                lb = f.read(2)
                if len(lb) < 2:
                    return ImageParser._err(info, 'segment',
                        f"JPEG: обрезан сегмент 0x{marker:04X}")
                length = int.from_bytes(lb, 'big')
                if length < 2:
                    return ImageParser._err(info, 'segment',
                        f"JPEG: битая длина сегмента 0x{marker:04X} = {length}")
                payload = length - 2
                f.seek(payload, 1)
        
        if not sof_found:
            return ImageParser._err(info, 'SOF',
                "JPEG: не найден маркер SOF (нет размеров)")
        
        tail_size = min(1024, file_size)
        f.seek(-tail_size, 2)
        tail = f.read(tail_size)
        
        if b'\xff\xd9' not in tail:
            return ImageParser._err(info, 'truncated',
                "JPEG: файл оборван — не найден маркер конца (EOI 0xFFD9)")
        
        return info

    @staticmethod
    def _parse_tiff(f, info):
        info['format'] = 'TIFF'
        info['compression'] = 'Lossless (TIFF)'
        info['size'] = "Заголовок корректен"
        info['depth'] = "8 бит × Каналы"
        info['dpi'] = "72x72 (дефолт)"
        info['filesize'] = "?"
        return info

    @staticmethod
    def _parse_pcx_raw(f, info, file_size):
        info['format'] = 'PCX'
        info['filesize'] = f"{file_size / 1024:.1f}"
        
        f.seek(0)
        pcx = f.read(128)
        if len(pcx) < 128:
            return ImageParser._err(info, 'PCX',
                f"PCX: обрезан заголовок (прочитано {len(pcx)} из 128)")
        
        info['compression'] = 'None' if pcx[2] == 0 else 'Lossless (RLE)'
        xmin = int.from_bytes(pcx[4:6], 'little')
        ymin = int.from_bytes(pcx[6:8], 'little')
        xmax = int.from_bytes(pcx[8:10], 'little')
        ymax = int.from_bytes(pcx[10:12], 'little')
        
        width = xmax - xmin + 1
        height = ymax - ymin + 1
        
        if width <= 0 or height <= 0:
            return ImageParser._err(info, 'PCX',
                f"PCX: некорректные размеры {width}x{height}")
        
        bpp = pcx[3]
        nplanes = pcx[65]
        
        info['size'] = f'{width}x{height}'
        info['depth'] = f'{bpp * nplanes} bit'
        info['dpi'] = "Не задано (72x72 дефолт)"
        return info


class ScanWorker(QThread):
    progress_updated = pyqtSignal(int, int)
    file_processed = pyqtSignal(dict)
    finished_scan = pyqtSignal(bool)

    GARBAGE_EXT = {'.tmp', '.part', '.crdownload', '.swp', '.log', '.lock'}

    def __init__(self, folder_path, enabled_formats):
        super().__init__()
        self.folder_path = folder_path
        self.enabled_formats = enabled_formats
        self._is_running = True
        self._is_paused = False
        self.mutex = QMutex()
        self.condition = QWaitCondition()

    def run(self):
        success = True
        try:
            files_to_process = self._collect_files()
            if not self._check_state():
                success = False
                return
            total_files = len(files_to_process)
            if total_files == 0:
                return
            for i, filepath in enumerate(files_to_process):
                if not self._check_state():
                    success = False
                    return
                try:
                    info = ImageParser.get_info(filepath)
                except Exception as e:
                    info = {
                        'name': os.path.basename(filepath),
                        'size': 'Ошибка', 'dpi': '-', 'depth': '-',
                        'compression': '-', 'filesize': '-',
                        'status': 'broken',
                        'error': f"Внутренняя ошибка: {type(e).__name__}: {e}",
                    }
                if self._check_state():
                    self.file_processed.emit(info)
                    self.progress_updated.emit(i + 1, total_files)
                else:
                    success = False
                    return
        finally:
            self.finished_scan.emit(success and self._is_running)

    def _collect_files(self):
        files = []
        def _scan(path):
            if not self._check_state():
                return
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        if not self._is_running:
                            return
                        try:
                            if entry.is_file(follow_symlinks=False):
                                ext = os.path.splitext(entry.name)[1].lower()
                                if ext in self.GARBAGE_EXT:
                                    continue
                                fmt = ext.lstrip('.').upper()
                                if fmt == 'JPE':
                                    fmt = 'JPEG'
                                if fmt not in self.enabled_formats:
                                    continue
                                if entry.stat(follow_symlinks=False).st_size == 0:
                                    continue
                                files.append(entry.path)
                            elif entry.is_dir(follow_symlinks=False):
                                _scan(entry.path)
                        except OSError:
                            continue
            except (PermissionError, OSError):
                return
        _scan(self.folder_path)
        return files

    def _check_state(self):
        self.mutex.lock()
        while self._is_paused and self._is_running:
            self.condition.wait(self.mutex)
        if not self._is_running:
            self.mutex.unlock()
            return False
        self.mutex.unlock()
        return True

    def pause(self):
        self.mutex.lock()
        self._is_paused = True
        self.mutex.unlock()

    def resume(self):
        self.mutex.lock()
        self._is_paused = False
        self.condition.wakeAll()
        self.mutex.unlock()

    def stop(self):
        self.mutex.lock()
        self._is_running = False
        self._is_paused = False
        self.condition.wakeAll()
        self.mutex.unlock()


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        headers = ['№', 'Имя файла', 'Размер (px)', 'Разрешение (dpi)',
                   'Глубина цвета', 'Сжатие', 'Размер файла (КБ)']
        self.tableResults.setHorizontalHeaderLabels(headers)
        self.tableResults.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tableResults.setRowCount(0)

        for chk in [self.chkJPG, self.chkGIF, self.chkTIF,
                    self.chkBMP, self.chkPNG, self.chkPCX]:
            chk.setChecked(True)

        self.selected_folder = None
        self.worker = None
        self.row_counter = 0

        self.btnSelectFolder.clicked.connect(self.select_folder)
        self.btnSelectFiles.clicked.connect(self.select_files)
        self.btnScan.clicked.connect(self.start_scan)
        self.btnExport.clicked.connect(self.export_csv)
        self.btnClear.clicked.connect(self.clear_table)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Выберите папку')
        if folder:
            self.selected_folder = folder
            self.lblStatus.setText(f'Папка: {folder}')

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, 'Выберите файлы', '',
            'Images (*.jpg *.jpeg *.gif *.tif *.tiff *.bmp *.png *.pcx)')
        if files:
            self._selected_files = files
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
        self.tableResults.setRowCount(0)
        self.row_counter = 0
        self.progressBar.setValue(0)
        formats = self.get_enabled_formats()
        if not formats:
            QMessageBox.warning(self, 'Внимание', 'Выберите хотя бы один формат!')
            return
        if hasattr(self, '_selected_files') and self._selected_files:
            files = self._selected_files
        elif self.selected_folder:
            files = None
        else:
            QMessageBox.warning(self, 'Внимание', 'Сначала выберите папку или файлы!')
            return
        self.btnScan.setEnabled(False)
        self.btnSelectFolder.setEnabled(False)
        self.btnSelectFiles.setEnabled(False)
        self.lblStatus.setText('Сканирование...')
        if files is not None:
            self._run_on_files(files, formats)
        else:
            self.worker = ScanWorker(self.selected_folder, formats)
            self.worker.progress_updated.connect(self.on_progress)
            self.worker.file_processed.connect(self.on_file_processed)
            self.worker.finished_scan.connect(self.on_finished)
            self.worker.start()

    def _run_on_files(self, files, formats):
        total = len(files)
        for i, filepath in enumerate(files):
            ext = os.path.splitext(filepath)[1].upper().lstrip('.')
            if ext == 'JPE':
                ext = 'JPEG'
            if ext not in formats:
                continue
            info = ImageParser.get_info(filepath)
            self.on_file_processed(info)
            self.on_progress(i + 1, total)
        self.on_finished(True)

    def on_progress(self, current, total):
        percent = int((current / total) * 100) if total > 0 else 0
        self.progressBar.setValue(percent)
        self.lblStatus.setText(f'Обработано {current} из {total}')

    def on_file_processed(self, info):
        row = self.row_counter
        self.tableResults.insertRow(row)
        self.tableResults.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.tableResults.setItem(row, 1, QTableWidgetItem(info['name']))
        self.tableResults.setItem(row, 2, QTableWidgetItem(info['size']))
        self.tableResults.setItem(row, 3, QTableWidgetItem(info['dpi']))
        self.tableResults.setItem(row, 4, QTableWidgetItem(info['depth']))
        self.tableResults.setItem(row, 5, QTableWidgetItem(info['compression']))
        self.tableResults.setItem(row, 6, QTableWidgetItem(info['filesize']))
        self.row_counter += 1

    def on_finished(self, success):
        self.label_2.setText(f'Всего файлов: {self.row_counter}')
        self.lblStatus.setText('Готово!' if success else 'Остановлено')
        self.btnScan.setEnabled(True)
        self.btnSelectFolder.setEnabled(True)
        self.btnSelectFiles.setEnabled(True)

    def clear_table(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        self.tableResults.setRowCount(0)
        self.row_counter = 0
        self.progressBar.setValue(0)
        self.lblStatus.setText('Готово')
        self.label_2.setText('lblStats')
        self.selected_folder = None
        if hasattr(self, '_selected_files'):
            self._selected_files = []

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
