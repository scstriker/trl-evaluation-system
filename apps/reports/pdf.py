"""Word 报告转 PDF：优先调用本机 Word（转换前刷新目录页码，版式还原度最高），其次 LibreOffice。

两者都没有时不生成 PDF，页面提示用户下载 Word 后自行另存为 PDF。
可用环境变量 TRL_PDF_CONVERTER 指定：auto（默认）/ word / libreoffice / none。
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

WORD = "word"
LIBREOFFICE = "libreoffice"
NONE = "none"
LABELS = {WORD: "Microsoft Word", LIBREOFFICE: "LibreOffice"}

_word_lock = threading.Lock()


class PdfError(Exception):
    pass


def _word_installed():
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            pass
        import win32com.client  # noqa: F401
    except (OSError, ImportError):
        return False
    return True


def _soffice_path():
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        candidate = Path(base) / "LibreOffice" / "program" / "soffice.exe"
        if base and candidate.exists():
            return str(candidate)
    return None


def detect_converter():
    preferred = getattr(settings, "REPORT_PDF_CONVERTER", "auto")
    if preferred == NONE:
        return None
    if preferred in (WORD, "auto") and _word_installed():
        return WORD
    if preferred in (LIBREOFFICE, "auto") and _soffice_path():
        return LIBREOFFICE
    return None


def _convert_with_word(docx_path, pdf_path):
    """用 Word 打开、刷新目录和域、存回 docx，再导出 PDF。"""
    import pythoncom
    import win32com.client

    with _word_lock:
        pythoncom.CoInitialize()
        word = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            document = word.Documents.Open(str(docx_path), ReadOnly=False, AddToRecentFiles=False, Visible=False)
            try:
                for index in range(1, document.TablesOfContents.Count + 1):
                    document.TablesOfContents(index).Update()
                document.Fields.Update()
                document.Save()
                document.ExportAsFixedFormat(str(pdf_path), 17, CreateBookmarks=1)  # 17 = PDF，1 = 按标题建书签
            finally:
                document.Close(False)
        except Exception as exc:  # noqa: BLE001  COM 错误类型繁多，统一转成 PdfError
            raise PdfError(f"调用 Word 转换失败：{exc}") from exc
        finally:
            if word is not None:
                try:
                    word.Quit()
                except Exception:  # noqa: BLE001
                    pass
            pythoncom.CoUninitialize()


def _convert_with_libreoffice(docx_path, pdf_path):
    soffice = _soffice_path()
    if not soffice:
        raise PdfError("未找到 LibreOffice。")
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf_path.parent), str(docx_path)],
            check=True,
            timeout=180,
            capture_output=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise PdfError(f"调用 LibreOffice 转换失败：{exc}") from exc
    produced = pdf_path.parent / (docx_path.stem + ".pdf")
    if produced != pdf_path:
        produced.replace(pdf_path)


def convert(docx_bytes, converter=None):
    """返回 (更新后的 docx 字节, pdf 字节, 转换方式)。

    Word 转换会刷新目录页码并把刷新后的 Word 一并返回；LibreOffice 不改动 Word 文件。
    """
    converter = converter or detect_converter()
    if converter is None:
        raise PdfError("本机未检测到 Word 或 LibreOffice，不能生成 PDF。")
    workdir = Path(tempfile.mkdtemp(prefix="trl-report-"))
    try:
        docx_path = workdir / "report.docx"
        pdf_path = workdir / "report.pdf"
        docx_path.write_bytes(docx_bytes)
        if converter == WORD:
            _convert_with_word(docx_path, pdf_path)
        else:
            _convert_with_libreoffice(docx_path, pdf_path)
        if not pdf_path.exists():
            raise PdfError("转换后没有得到 PDF 文件。")
        return docx_path.read_bytes(), pdf_path.read_bytes(), converter
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
