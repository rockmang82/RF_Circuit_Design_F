"""
Circuit_MC_Simulation.py
Monte Carlo Simulation Module for RF Circuit Impedance Analysis
PyQt5 + matplotlib + numpy (scipy 미사용, math.erf 활용)
"""

import copy
import math
import io
import numpy as np

from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTabWidget, QWidget, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressDialog, QMenu, QFrame, QScrollArea,
    QMessageBox, QLineEdit, QDialogButtonBox, QFileDialog, QPushButton,
    QRadioButton, QGroupBox, QButtonGroup,
)
from PyQt5.QtCore import Qt, QThread, QObject, QEventLoop, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QImage, QPixmap, QDoubleValidator, QIntValidator

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt


# ──────────────────────────────────────────────
# 통계 헬퍼 함수 (scipy 미사용)
# ──────────────────────────────────────────────

def _norm_pdf(x, mu, sigma):
    """정규분포 확률밀도함수"""
    return np.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * math.sqrt(2 * math.pi))


def _sigma_coverage_theory(k):
    """±k*sigma 범위 내 정규분포 이론 확률 (math.erf 활용)"""
    return math.erf(k / math.sqrt(2))


def _skewness(data):
    """Fisher 왜도 (편향 보정 없음)"""
    mu = np.mean(data)
    sigma = np.std(data, ddof=1)
    if sigma == 0:
        return 0.0
    return float(np.mean(((data - mu) / sigma) ** 3))


def _kurtosis_excess(data):
    """초과 첨도 (정규분포 = 0)"""
    mu = np.mean(data)
    sigma = np.std(data, ddof=1)
    if sigma == 0:
        return 0.0
    return float(np.mean(((data - mu) / sigma) ** 4)) - 3.0


# ──────────────────────────────────────────────
# Monte Carlo 백그라운드 워커
# ──────────────────────────────────────────────

class MCWorker(QObject):
    """Monte Carlo 시뮬레이션 백그라운드 계산 워커"""
    progress = pyqtSignal(int)              # 0~100
    finished = pyqtSignal(list, float)      # (|Z| 결과 리스트, z_nominal)
    error = pyqtSignal(str)

    def __init__(self, components, wires, f_hz, n_runs, mna_func):
        super().__init__()
        self._comps = copy.deepcopy(components)
        self._wires = wires
        self._f_hz = f_hz
        self._n_runs = n_runs
        self._mna = mna_func
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # 기준 임피던스 계산
            z_nom = self._mna(self._comps, self._wires, self._f_hz)
            if z_nom is None:
                self.error.emit('기준 임피던스 계산 실패 — Port 연결을 확인하세요')
                return

            results = []
            report_step = max(1, self._n_runs // 100)

            for i in range(self._n_runs):
                if self._cancelled:
                    return

                # 소자 값 섭동 (정규분포, 3σ = Tolerance%)
                perturbed = copy.deepcopy(self._comps)
                for comp in perturbed:
                    if comp.value is not None and comp.value != 0:
                        tol = getattr(comp, 'tolerance', 1.0)
                        sigma_frac = tol / 3.0 / 100.0
                        comp.value *= float(np.random.normal(1.0, sigma_frac))

                Z = self._mna(perturbed, self._wires, self._f_hz)
                if Z is not None:
                    results.append(abs(Z))

                if (i + 1) % report_step == 0:
                    self.progress.emit(int((i + 1) / self._n_runs * 100))

            self.progress.emit(100)
            self.finished.emit(results, abs(z_nom))

        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# Monte Carlo 결과 창
# ──────────────────────────────────────────────

class MCResultsWindow(QDialog):
    """Monte Carlo Simulation Results"""

    def __init__(self, f_mhz, n_runs, results, z_nominal, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Monte Carlo Simulation Results')
        self.setWindowFlags(
            (self.windowFlags() | Qt.Window) & ~Qt.WindowContextHelpButtonHint
        )
        self.resize(1100, 700)
        self.setMinimumSize(800, 500)

        self._f_mhz = f_mhz
        self._n_runs = n_runs
        self._data = np.array(results, dtype=float)
        self._z_nominal = z_nominal
        self._n_valid = len(self._data)
        self._mu = float(np.mean(self._data))
        self._sigma = float(np.std(self._data, ddof=1))
        self._hist_xlim = None   # None = 자동, (min, max) = 사용자 지정
        self._lsl = None         # None = 미설정
        self._usl = None

        self._build_ui()

    # ── 단위 포맷 헬퍼 ──
    @staticmethod
    def _fmt(val):
        a = abs(val)
        if a == 0:
            return '0.000 Ω'
        if a < 1e-6:
            return f'{val * 1e9:.4f} nΩ'
        if a < 1e-3:
            return f'{val * 1e6:.4f} μΩ'
        if a < 1.0:
            return f'{val * 1e3:.4f} mΩ'
        return f'{val:.4f} Ω'

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 헤더
        header = QLabel(
            f'분석 주파수: {self._f_mhz:.3f} MHz   |   '
            f'시뮬레이션 횟수: {self._n_runs:,}회   |   '
            f'유효 결과: {self._n_valid:,}개   |   '
            f'Z_nominal: {self._z_nominal:.6f} Ω'
        )
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        header.setFont(font)
        header.setStyleSheet('padding: 4px 2px;')
        layout.addWidget(header)

        # 탭
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_histogram_tab(), 'Histogram')
        self._tabs.addTab(self._build_statistics_tab(), 'Statistics')
        self._tabs.addTab(self._build_capability_tab(), 'Process Capability')
        layout.addWidget(self._tabs, stretch=1)

    # ── Tab 1: Histogram ──

    def _build_histogram_tab(self):
        widget = QWidget()
        vbox = QVBoxLayout(widget)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)

        # ── 상단 버튼 바 (우측 정렬) ──
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 0, 2, 0)
        btn_bar.addStretch()
        self._lsl_usl_btn = QPushButton('LSL / USL 설정')
        self._lsl_usl_btn.setFixedSize(130, 28)
        self._lsl_usl_btn.setStyleSheet(
            'background-color: #4A90D9; color: white; font-size: 10pt; border-radius: 3px;'
        )
        self._lsl_usl_btn.clicked.connect(self._show_lsl_usl_dialog)
        btn_bar.addWidget(self._lsl_usl_btn)
        vbox.addLayout(btn_bar)

        self.hist_fig = Figure(facecolor='white')
        self.hist_canvas = FigureCanvas(self.hist_fig)
        vbox.addWidget(self.hist_canvas, stretch=1)

        # 우클릭 컨텍스트 메뉴
        self.hist_canvas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.hist_canvas.customContextMenuRequested.connect(self._on_hist_right_click)

        # X축 더블클릭 → 범위 설정 팝업
        self.hist_canvas.mpl_connect('button_press_event', self._on_hist_mouse_press)

        self._plot_histogram()
        return widget

    def _plot_histogram(self):
        self.hist_fig.clear()
        ax = self.hist_fig.add_subplot(111)

        data = self._data
        mu, sigma = self._mu, self._sigma
        n = self._n_valid

        # 자동 bin 수
        n_bins = max(30, min(int(math.sqrt(n)), 100))

        # 히스토그램
        ax.hist(
            data, bins=n_bins, density=True,
            color='#5C9BD6', alpha=0.7, edgecolor='white', linewidth=0.4,
            label=f'MC Histogram  (N={n:,})'
        )

        # 정규분포 오버레이
        x_fit = np.linspace(data.min(), data.max(), 600)
        pdf = _norm_pdf(x_fit, mu, sigma)
        ax.plot(x_fit, pdf, color='#E53935', lw=2.0,
                label=f'Normal fit  μ={self._fmt(mu)}, σ={self._fmt(sigma)}')

        # ── 히스토그램·정규분포 곡선 기준으로 X 범위 확정 (axvline이 범위를 바꾸기 전에 저장) ──
        if self._hist_xlim is not None:
            x_lo, x_hi = self._hist_xlim
        else:
            margin = (data.max() - data.min()) * 0.05
            x_lo = data.min() - margin
            x_hi = data.max() + margin

        # 수직 마커선
        ax.axvline(self._z_nominal, color='black', ls='--', lw=1.5,
                   label=f'Z_nominal = {self._fmt(self._z_nominal)}')
        ax.axvline(mu, color='#1565C0', ls='-', lw=1.8,
                   label=f'μ = {self._fmt(mu)}')
        for k, color, ls, lw in [
            (1, '#2E7D32', '--', 1.3),
            (2, '#E65100', '--', 1.1),
            (3, '#C62828', ':',  1.1),
        ]:
            ax.axvline(mu + k * sigma, color=color, ls=ls, lw=lw,
                       label=f'±{k}σ = ±{self._fmt(k * sigma)}')
            ax.axvline(mu - k * sigma, color=color, ls=ls, lw=lw)

        # LSL / USL 선 (설정된 경우)
        if self._lsl is not None:
            ax.axvline(self._lsl, color='#424242', ls='--', lw=2.8,
                       label=f'LSL = {self._fmt(self._lsl)}', zorder=5)
        if self._usl is not None:
            ax.axvline(self._usl, color='#424242', ls='--', lw=2.8,
                       label=f'USL = {self._fmt(self._usl)}', zorder=5)

        # X축 범위 강제 적용 (axvline 이후 스케일 변경 방지)
        ax.set_xlim(x_lo, x_hi)

        if self._hist_xlim is not None:
            xlim_hint = f'  [Range: {x_lo:.4g} ~ {x_hi:.4g} Ohm]  (double-click to change)'
        else:
            xlim_hint = '  (double-click X-axis to set range)'

        ax.set_xlabel(f'|Z| (Ohm){xlim_hint}', fontsize=9)
        ax.set_ylabel('Probability Density', fontsize=10)
        ax.set_title(
            f'Monte Carlo |Z| Distribution @ {self._f_mhz:.3f} MHz', fontsize=11
        )
        ax.legend(fontsize=7.5, loc='upper right', framealpha=0.9)
        ax.grid(True, color='#E0E0E0', linestyle='--', alpha=0.7)
        ax.set_facecolor('white')

        self.hist_fig.tight_layout()
        self.hist_canvas.draw()

    def _on_hist_mouse_press(self, event):
        """X축 영역 더블클릭 → X축 범위 설정 팝업"""
        if not event.dblclick:
            return
        if not self.hist_fig.axes:
            return
        ax = self.hist_fig.axes[0]
        # axes의 디스플레이 좌표 bounding box
        bbox = ax.get_window_extent()
        # X축 영역: axes 너비 범위 내, axes 하단 경계 아래
        if bbox.x0 <= event.x <= bbox.x1 and event.y < bbox.y0:
            self._show_xlim_dialog(ax)

    def _show_xlim_dialog(self, ax):
        """X축 범위 설정 팝업"""
        cur_min, cur_max = ax.get_xlim()

        dlg = QDialog(self)
        dlg.setWindowTitle('X축 범위 설정')
        dlg.setFixedWidth(300)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        hint = QLabel('X축(|Z| 범위)의 최솟값과 최댓값을 입력하세요.')
        hint.setStyleSheet('color: #555; font-size: 9pt;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)

        min_edit = QLineEdit(f'{cur_min:.6g}')
        min_edit.setValidator(QDoubleValidator(-1e15, 1e15, 8))
        min_edit.setFixedWidth(160)

        max_edit = QLineEdit(f'{cur_max:.6g}')
        max_edit.setValidator(QDoubleValidator(-1e15, 1e15, 8))
        max_edit.setFixedWidth(160)

        form.addRow('Min (Ω):', min_edit)
        form.addRow('Max (Ω):', max_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        apply_btn = QPushButton('적용')
        apply_btn.setStyleSheet(
            'background-color: #4A90D9; color: white; font-size: 10pt; border-radius: 3px;'
        )
        apply_btn.setFixedHeight(28)
        reset_btn = QPushButton('자동 초기화')
        reset_btn.setFixedHeight(28)
        cancel_btn = QPushButton('취소')
        cancel_btn.setFixedHeight(28)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        def do_apply():
            try:
                xmin = float(min_edit.text().strip())
                xmax = float(max_edit.text().strip())
            except ValueError:
                QMessageBox.warning(dlg, '입력 오류', '올바른 숫자를 입력하세요.')
                return
            if xmin >= xmax:
                QMessageBox.warning(dlg, '입력 오류', 'Min 값은 Max 값보다 작아야 합니다.')
                return
            self._hist_xlim = (xmin, xmax)
            self._plot_histogram()
            dlg.accept()

        def do_reset():
            self._hist_xlim = None
            self._plot_histogram()
            dlg.accept()

        apply_btn.clicked.connect(do_apply)
        reset_btn.clicked.connect(do_reset)
        cancel_btn.clicked.connect(dlg.reject)
        min_edit.returnPressed.connect(lambda: max_edit.setFocus())
        max_edit.returnPressed.connect(do_apply)

        dlg.exec_()

    def _on_hist_right_click(self, pos):
        menu = QMenu(self)
        copy_act = menu.addAction('클립보드에 이미지 복사')
        save_act = menu.addAction('이미지 파일로 저장...')
        action = menu.exec_(self.hist_canvas.mapToGlobal(pos))
        if action == copy_act:
            self._copy_hist_to_clipboard()
        elif action == save_act:
            self._save_hist_to_file()

    def _copy_hist_to_clipboard(self):
        buf = io.BytesIO()
        self.hist_fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        img = QImage()
        img.loadFromData(buf.read())
        QApplication.clipboard().setPixmap(QPixmap.fromImage(img))

    def _save_hist_to_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, '히스토그램 저장', 'mc_histogram',
            'PNG (*.png);;SVG (*.svg);;PDF (*.pdf);;All Files (*)'
        )
        if path:
            self.hist_fig.savefig(path, dpi=150, bbox_inches='tight')

    # ── LSL / USL 설정 다이얼로그 ──

    def _show_lsl_usl_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('LSL / USL 설정')
        dlg.setFixedWidth(380)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        # ── 모드 선택 (라디오 버튼) ──
        mode_group = QGroupBox('설정 모드')
        mode_vbox = QVBoxLayout(mode_group)
        mode_vbox.setSpacing(6)

        radio_pct    = QRadioButton('평균 μ의 % 비율로 설정  (대칭)')
        radio_direct = QRadioButton('직접 입력 (Ω)')
        radio_pct.setChecked(True)

        btn_grp = QButtonGroup(dlg)
        btn_grp.addButton(radio_pct,    0)
        btn_grp.addButton(radio_direct, 1)

        mode_vbox.addWidget(radio_pct)
        mode_vbox.addWidget(radio_direct)
        layout.addWidget(mode_group)

        # ── % 모드 입력 위젯 ──
        pct_widget = QWidget()
        pct_form   = QFormLayout(pct_widget)
        pct_form.setContentsMargins(4, 4, 4, 4)
        pct_form.setSpacing(8)

        pct_edit = QLineEdit()
        pct_edit.setValidator(QDoubleValidator(0.001, 99.999, 4))
        pct_edit.setPlaceholderText('예: 10.0')
        # 현재 값 복원
        if self._lsl is not None and self._mu != 0:
            restored = round((1.0 - self._lsl / self._mu) * 100, 4)
            pct_edit.setText(f'{restored:.4g}')
        pct_form.addRow('±% 비율:', pct_edit)

        hint_lbl = QLabel()
        hint_lbl.setStyleSheet('color: #1565C0; font-size: 9pt;')

        def update_hint():
            try:
                p = float(pct_edit.text())
                lsl_v = self._mu * (1.0 - p / 100.0)
                usl_v = self._mu * (1.0 + p / 100.0)
                hint_lbl.setText(
                    f'→  LSL = {self._fmt(lsl_v)},  USL = {self._fmt(usl_v)}'
                )
            except ValueError:
                hint_lbl.setText('')

        pct_edit.textChanged.connect(update_hint)
        update_hint()
        pct_form.addRow('', hint_lbl)
        layout.addWidget(pct_widget)

        # ── 직접 입력 위젯 ──
        direct_widget = QWidget()
        direct_form   = QFormLayout(direct_widget)
        direct_form.setContentsMargins(4, 4, 4, 4)
        direct_form.setSpacing(8)

        lsl_edit = QLineEdit()
        usl_edit = QLineEdit()
        lsl_edit.setValidator(QDoubleValidator(-1e15, 1e15, 8))
        usl_edit.setValidator(QDoubleValidator(-1e15, 1e15, 8))
        lsl_edit.setPlaceholderText('LSL (Ω)')
        usl_edit.setPlaceholderText('USL (Ω)')
        if self._lsl is not None:
            lsl_edit.setText(f'{self._lsl:.6g}')
        if self._usl is not None:
            usl_edit.setText(f'{self._usl:.6g}')
        direct_form.addRow('LSL (Ω):', lsl_edit)
        direct_form.addRow('USL (Ω):', usl_edit)
        layout.addWidget(direct_widget)
        direct_widget.setVisible(False)

        # 모드 전환
        def on_mode_changed(btn_id):
            pct_widget.setVisible(btn_id == 0)
            direct_widget.setVisible(btn_id == 1)
            dlg.adjustSize()

        btn_grp.idClicked.connect(on_mode_changed)

        # ── 버튼 행 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        ok_btn    = QPushButton('OK')
        reset_btn = QPushButton('초기화')
        cancel_btn = QPushButton('취소')

        ok_btn.setFixedHeight(30)
        reset_btn.setFixedHeight(30)
        cancel_btn.setFixedHeight(30)

        btn_row.addWidget(ok_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        # ── 슬롯 ──
        def do_ok():
            if btn_grp.checkedId() == 0:
                # % 모드
                try:
                    p = float(pct_edit.text())
                except ValueError:
                    QMessageBox.warning(dlg, '입력 오류', '% 값을 숫자로 입력하세요.')
                    return
                if not (0 < p < 100):
                    QMessageBox.warning(dlg, '입력 오류', '% 값은 0 초과 100 미만이어야 합니다.')
                    return
                self._lsl = self._mu * (1.0 - p / 100.0)
                self._usl = self._mu * (1.0 + p / 100.0)
            else:
                # 직접 입력 모드
                try:
                    lsl_v = float(lsl_edit.text())
                    usl_v = float(usl_edit.text())
                except ValueError:
                    QMessageBox.warning(dlg, '입력 오류', 'LSL / USL 값을 숫자로 입력하세요.')
                    return
                if lsl_v >= usl_v:
                    QMessageBox.warning(dlg, '입력 오류', 'LSL은 USL보다 작아야 합니다.')
                    return
                self._lsl = lsl_v
                self._usl = usl_v

            self._plot_histogram()
            self._refresh_capability_tab()
            dlg.accept()

        def do_reset():
            self._lsl = None
            self._usl = None
            self._plot_histogram()
            self._refresh_capability_tab()
            dlg.accept()

        ok_btn.clicked.connect(do_ok)
        reset_btn.clicked.connect(do_reset)
        cancel_btn.clicked.connect(dlg.reject)
        pct_edit.returnPressed.connect(do_ok)

        dlg.exec_()

    def _refresh_capability_tab(self):
        """Process Capability 탭 콘텐츠를 LSL/USL 상태에 맞게 재구성"""
        self._cap_scroll.setWidget(self._build_capability_content())

    # ── Tab 2: Statistics ──

    def _build_statistics_tab(self):
        data = self._data
        mu, sigma = self._mu, self._sigma
        n = self._n_valid

        cv = sigma / mu * 100 if mu != 0 else 0.0
        se = sigma / math.sqrt(n) if n > 0 else 0.0
        ci_lo = mu - 1.96 * se
        ci_hi = mu + 1.96 * se
        skew = _skewness(data)
        kurt = _kurtosis_excess(data)
        dev_from_nom = (mu - self._z_nominal) / self._z_nominal * 100 if self._z_nominal != 0 else 0.0

        rows = [
            ('시뮬레이션 횟수 (N)', f'{n:,}'),
            ('Z_nominal (기준값)', f'{self._z_nominal:.6f} Ω'),
            ('평균 (μ)', f'{mu:.6f} Ω'),
            ('표준편차 (σ)', f'{sigma:.6f} Ω'),
            ('변동계수 (CV = σ/μ)', f'{cv:.4f} %'),
            ('최솟값', f'{data.min():.6f} Ω'),
            ('최댓값', f'{data.max():.6f} Ω'),
            ('범위 (Range)', f'{data.max() - data.min():.6f} Ω'),
            ('중앙값 (Median)', f'{float(np.median(data)):.6f} Ω'),
            ('왜도 (Skewness)', f'{skew:.5f}'),
            ('초과 첨도 (Excess Kurtosis)', f'{kurt:.5f}'),
            ('표준오차 (SE = σ/√N)', f'{se:.6f} Ω'),
            ('95% CI 하한', f'{ci_lo:.6f} Ω'),
            ('95% CI 상한', f'{ci_hi:.6f} Ω'),
            ('μ vs Z_nominal 편차', f'{dev_from_nom:+.4f} %'),
        ]

        widget = QWidget()
        vbox = QVBoxLayout(widget)
        vbox.setContentsMargins(12, 12, 12, 12)

        table = QTableWidget(len(rows), 2)
        table.setHorizontalHeaderLabels(['항목', '값'])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)

        for i, (label, value) in enumerate(rows):
            lbl_item = QTableWidgetItem(label)
            lbl_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            val_item = QTableWidgetItem(value)
            val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(i, 0, lbl_item)
            table.setItem(i, 1, val_item)

        vbox.addWidget(table)
        return widget

    # ── Tab 3: Process Capability ──

    def _build_capability_tab(self):
        """외부 컨테이너만 생성 후 스크롤 콘텐츠를 동적으로 채운다."""
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)

        self._cap_scroll = QScrollArea()
        self._cap_scroll.setWidgetResizable(True)
        self._cap_scroll.setFrameShape(QFrame.NoFrame)
        self._cap_scroll.setWidget(self._build_capability_content())

        outer.addWidget(self._cap_scroll)
        return widget

    def _build_capability_content(self):
        """LSL/USL 상태에 따라 Process Capability 탭 내용을 구성하여 반환."""
        data = self._data
        mu, sigma = self._mu, self._sigma
        n = self._n_valid

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(14)

        # ── 시그마 수준 섹션 ──
        cov_frame = QFrame()
        cov_frame.setStyleSheet(
            'QFrame { background-color: #F8F9FA; border-radius: 8px; border: 1px solid #DEE2E6; }'
        )
        cov_vbox = QVBoxLayout(cov_frame)
        cov_vbox.setContentsMargins(12, 10, 12, 14)
        cov_vbox.setSpacing(10)

        cov_title = QLabel('시그마 수준 분석 (Sigma Coverage)')
        cov_title.setStyleSheet(
            'font-size: 12pt; font-weight: bold; color: #1565C0; border: none;'
        )
        cov_vbox.addWidget(cov_title)

        k_values = [1, 2, 3, 4, 5, 6]
        sig_rows = []
        sigma_level_est = 0.0

        for k in k_values:
            lo, hi = mu - k * sigma, mu + k * sigma
            count_in = int(np.sum((data >= lo) & (data <= hi)))
            pct_actual = count_in / n * 100 if n > 0 else 0.0
            pct_theory = _sigma_coverage_theory(k) * 100
            sig_rows.append((
                f'±{k}σ',
                f'{lo:.5f} ~ {hi:.5f} Ω',
                f'{count_in:,}',
                f'{pct_actual:.3f}%',
                f'{pct_theory:.3f}%',
            ))
            if pct_actual >= pct_theory * 0.999:
                sigma_level_est = float(k)

        sig_table = QTableWidget(len(sig_rows), 5)
        sig_table.setHorizontalHeaderLabels(
            ['범위', '구간 (Ω)', '포함 횟수', '실제 (%)', '이론 (%)']
        )
        sig_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        sig_table.setEditTriggers(QTableWidget.NoEditTriggers)
        sig_table.verticalHeader().setVisible(False)
        sig_table.setAlternatingRowColors(True)

        for row_i, row_data in enumerate(sig_rows):
            for col_i, text in enumerate(row_data):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                sig_table.setItem(row_i, col_i, item)

        cov_vbox.addWidget(sig_table)

        sigma_lbl = QLabel(f'추정 시그마 수준: ≥ {sigma_level_est:.0f}σ')
        sigma_lbl.setStyleSheet(
            'font-size: 14pt; font-weight: bold; color: #1565C0; border: none; padding: 4px 0;'
        )
        sigma_lbl.setAlignment(Qt.AlignCenter)
        cov_vbox.addWidget(sigma_lbl)
        vbox.addWidget(cov_frame)

        # ── 공정 능력지수 섹션 ──
        cpk_frame = QFrame()
        cpk_vbox = QVBoxLayout(cpk_frame)
        cpk_vbox.setContentsMargins(12, 10, 12, 14)
        cpk_vbox.setSpacing(10)

        if self._lsl is None or self._usl is None:
            # LSL/USL 미설정 — 안내 플레이스홀더
            cpk_frame.setStyleSheet(
                'QFrame { background-color: #FFF8E1; border-radius: 8px; border: 1px solid #FFE082; }'
            )
            cpk_title = QLabel('공정 능력 지수 (Process Capability Index)')
            cpk_title.setStyleSheet(
                'font-size: 12pt; font-weight: bold; color: #F57F17; border: none;'
            )
            cpk_vbox.addWidget(cpk_title)

            note = QLabel(
                '※  Histogram 탭의 [LSL / USL 설정] 버튼으로\n'
                '     규격 한계(LSL / USL)를 설정하면 공정 능력지수가 자동 계산됩니다.'
            )
            note.setStyleSheet('font-size: 10pt; color: #795548; border: none;')
            note.setWordWrap(True)
            cpk_vbox.addWidget(note)

        else:
            # LSL/USL 설정됨 — 지수 계산 및 표시
            lsl, usl = self._lsl, self._usl

            cpk_frame.setStyleSheet(
                'QFrame { background-color: #E8F5E9; border-radius: 8px; border: 1px solid #A5D6A7; }'
            )
            cpk_title = QLabel('공정 능력 지수 (Process Capability Index)')
            cpk_title.setStyleSheet(
                'font-size: 12pt; font-weight: bold; color: #1B5E20; border: none;'
            )
            cpk_vbox.addWidget(cpk_title)

            # ── 계산 ──
            cp  = (usl - lsl) / (6 * sigma) if sigma > 0 else float('inf')
            cpu = (usl - mu)  / (3 * sigma) if sigma > 0 else float('inf')
            cpl = (mu  - lsl) / (3 * sigma) if sigma > 0 else float('inf')
            cpk = min(cpu, cpl)

            T   = (usl + lsl) / 2.0
            cpm = ((usl - lsl) / (6 * math.sqrt(sigma ** 2 + (mu - T) ** 2))
                   if sigma > 0 else float('inf'))

            sigma_level_cpk = cpk * 3

            def _norm_cdf(x):
                return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

            p_in  = _norm_cdf((usl - mu) / sigma) - _norm_cdf((lsl - mu) / sigma)
            ppm_theory = max(0.0, (1.0 - p_in) * 1e6)

            oob_cnt = int(np.sum((data < lsl) | (data > usl)))
            ppm_actual = oob_cnt / n * 1e6 if n > 0 else 0.0

            # ── 규격 요약 레이블 ──
            spec_lbl = QLabel(
                f'LSL = {self._fmt(lsl)}   |   USL = {self._fmt(usl)}'
                f'   |   규격 폭 = {self._fmt(usl - lsl)}'
            )
            spec_lbl.setStyleSheet('font-size: 10pt; color: #2E7D32; border: none;')
            spec_lbl.setAlignment(Qt.AlignCenter)
            cpk_vbox.addWidget(spec_lbl)

            # ── 지수 테이블 ──
            def _cpk_judge(v):
                if v >= 1.67:   return '매우 우수 (≥1.67)'
                if v >= 1.33:   return '우수 (≥1.33)'
                if v >= 1.00:   return '적합 (≥1.00)'
                if v >= 0.67:   return '주의 (≥0.67)'
                return '부적합 (<0.67)'

            idx_rows = [
                ('Cp  (공정 능력)',          f'{cp:.4f}',  _cpk_judge(cp)),
                ('Cpu (상측 공정 능력)',      f'{cpu:.4f}', _cpk_judge(cpu)),
                ('Cpl (하측 공정 능력)',      f'{cpl:.4f}', _cpk_judge(cpl)),
                ('Cpk (최소 공정 능력)',      f'{cpk:.4f}', _cpk_judge(cpk)),
                ('Cpm (Taguchi 지수)',        f'{cpm:.4f}', _cpk_judge(cpm)),
                ('σ 수준 (= Cpk × 3)',        f'{sigma_level_cpk:.3f} σ', ''),
                ('PPM — 이론 (정규분포)',     f'{ppm_theory:,.1f} ppm', ''),
                ('PPM — 실측 (MC 데이터)',    f'{ppm_actual:,.1f} ppm  ({oob_cnt:,}건)', ''),
            ]

            idx_table = QTableWidget(len(idx_rows), 3)
            idx_table.setHorizontalHeaderLabels(['지수', '값', '판정'])
            idx_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            idx_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            idx_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            idx_table.setEditTriggers(QTableWidget.NoEditTriggers)
            idx_table.verticalHeader().setVisible(False)
            idx_table.setAlternatingRowColors(True)

            # 판정에 따른 행 색상
            color_map = {
                '매우 우수': QColor('#C8E6C9'),
                '우수':     QColor('#DCEDC8'),
                '적합':     QColor('#FFF9C4'),
                '주의':     QColor('#FFE0B2'),
                '부적합':   QColor('#FFCDD2'),
            }

            for ri, (label, val, judge) in enumerate(idx_rows):
                bg = None
                for key, color in color_map.items():
                    if key in judge:
                        bg = color
                        break

                for ci, text in enumerate([label, val, judge]):
                    it = QTableWidgetItem(text)
                    it.setTextAlignment(
                        Qt.AlignLeft | Qt.AlignVCenter if ci == 0
                        else Qt.AlignCenter
                    )
                    if bg:
                        it.setBackground(bg)
                    idx_table.setItem(ri, ci, it)

            cpk_vbox.addWidget(idx_table)

            # ── 종합 판정 배너 ──
            judge_text  = _cpk_judge(cpk)
            banner_color = '#1B5E20' if cpk >= 1.33 else ('#F57F17' if cpk >= 1.00 else '#B71C1C')
            banner = QLabel(f'종합 판정 (Cpk 기준):  {judge_text}')
            banner.setStyleSheet(
                f'font-size: 13pt; font-weight: bold; color: {banner_color};'
                f' border: none; padding: 6px 0;'
            )
            banner.setAlignment(Qt.AlignCenter)
            cpk_vbox.addWidget(banner)

        vbox.addWidget(cpk_frame)
        vbox.addStretch()
        return inner


# ──────────────────────────────────────────────
# 진입점 함수 (MainWindow에서 호출)
# ──────────────────────────────────────────────

def open_mc_analysis(parent, components, wires, mna_func):
    """
    Monte Carlo 시뮬레이션 실행 진입점.
    parent        : QWidget (MainWindow)
    components    : list[Component]
    wires         : list[Wire]
    mna_func      : callable — mna_solve_one(components, wires, f_hz) → complex | None

    비동기 패턴 (QEventLoop 미사용):
    스레드 시작 후 즉시 반환. 완료 시 신호/슬롯으로 결과창 팝업.
    """
    # ── 입력 다이얼로그 ──
    dlg = QDialog(parent)
    dlg.setWindowTitle('Monte Carlo Simulation 설정')
    dlg.setFixedWidth(340)
    dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)

    form = QFormLayout(dlg)
    form.setContentsMargins(16, 16, 16, 12)
    form.setSpacing(10)

    freq_edit = QLineEdit('13.56')
    freq_edit.setValidator(QDoubleValidator(0.001, 1e6, 4))
    form.addRow('해석 주파수 (MHz):', freq_edit)

    runs_edit = QLineEdit('1000')
    runs_edit.setValidator(QIntValidator(100, 1000000))
    form.addRow('시뮬레이션 횟수:', runs_edit)

    note_lbl = QLabel('※ 정규분포 샘플링  (3σ = 각 소자의 Tolerance%)')
    note_lbl.setStyleSheet('color: #666; font-size: 9pt;')
    form.addRow('', note_lbl)

    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    form.addRow(btns)

    if dlg.exec_() != QDialog.Accepted:
        return

    try:
        f_mhz = float(freq_edit.text().strip())
        n_runs = int(runs_edit.text().strip())
    except ValueError:
        QMessageBox.warning(parent, '입력 오류', '올바른 숫자를 입력하세요')
        return

    if f_mhz <= 0:
        QMessageBox.warning(parent, '입력 오류', '주파수는 0보다 커야 합니다')
        return
    if n_runs < 100:
        QMessageBox.warning(parent, '입력 오류', '시뮬레이션 횟수는 최소 100 이상이어야 합니다')
        return

    # ── Progress Dialog ──
    progress = QProgressDialog(
        'Monte Carlo 시뮬레이션 준비 중...', '취소', 0, 100, parent
    )
    progress.setWindowTitle('Monte Carlo 시뮬레이션')
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setMinimumWidth(380)
    progress.setValue(0)
    progress.show()

    # ── Thread + Worker ──
    thread = QThread(parent)
    worker = MCWorker(components, wires, f_mhz * 1e6, n_runs, mna_func)
    worker.moveToThread(thread)

    # GC 방지: parent 속성에 참조 보관
    parent._mc_thread = thread
    parent._mc_worker = worker
    parent._mc_progress = progress

    def on_progress(val):
        if progress.wasCanceled():
            worker.cancel()
            return
        progress.setValue(val)
        done = int(val * n_runs / 100)
        progress.setLabelText(
            f'Monte Carlo 시뮬레이션 중...\n'
            f'{done:,} / {n_runs:,} 완료'
        )

    def on_finished(results, z_nom):
        progress.close()
        if not results:
            QMessageBox.warning(parent, 'MC 시뮬레이션 오류', '유효한 시뮬레이션 결과가 없습니다')
            return
        win = MCResultsWindow(f_mhz, n_runs, results, z_nom, parent)
        parent._mc_win = win   # GC 방지
        win.show()

    def on_error(msg):
        progress.close()
        QMessageBox.warning(parent, 'MC 시뮬레이션 오류', msg)

    def on_canceled():
        worker.cancel()

    progress.canceled.connect(on_canceled)
    thread.started.connect(worker.run)
    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)

    thread.start()
