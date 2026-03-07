"""
Circuit_SmithChart.py
RF 회로 설계 및 Smith Chart 분석기
PyQt5 + matplotlib + numpy 기반 단일 파일 애플리케이션
"""

import sys
import math
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QPushButton, QLabel, QLineEdit,
    QMessageBox, QInputDialog, QDialog, QComboBox, QProgressDialog,
    QSizePolicy, QStatusBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QMenu
)
from PyQt5.QtCore import (
    Qt, QPointF, QRectF, QThread, pyqtSignal, QObject
)
from PyQt5.QtGui import (
    QPainter, QPen, QBrush, QColor, QPainterPath, QFont, QCursor,
    QDoubleValidator
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import json


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

class Component:
    """회로 소자 (R, L, C) 데이터 모델"""
    _id_counter = 0

    def __init__(self, comp_type, x, y):
        Component._id_counter += 1
        self.id = Component._id_counter
        self.type = comp_type       # 'R', 'L', 'C'
        self.x = x                  # 중심 x (스냅 적용)
        self.y = y                  # 중심 y (스냅 적용)
        self.value = None           # 숫자값 (None = 미입력)
        self.horizontal = True      # True=수평, False=수직
        self.selected = False
        self.error_highlight = False
        self.name = None            # 예: "R1", "C2", "L1" — 배치 시 자동 부여

    def get_pins(self):
        """소자의 두 핀 좌표 반환 [(x1,y1), (x2,y2)]"""
        if self.horizontal:
            return [(self.x - 30, self.y), (self.x + 30, self.y)]
        else:
            return [(self.x, self.y - 30), (self.x, self.y + 30)]

    def get_label(self):
        """소자 값 레이블 문자열"""
        if self.value is None:
            return '?'
        if self.type == 'R':
            return f'{self.value}Ω'
        elif self.type == 'L':
            return f'{self.value}nH'
        elif self.type == 'C':
            return f'{self.value}pF'
        return '?'

    def admittance(self, f):
        """주파수 f(Hz)에서 소자의 어드미턴스 Y 반환"""
        if self.value is None or self.value == 0:
            return 0j
        if self.type == 'R':
            return 1.0 / self.value
        elif self.type == 'L':
            L = self.value * 1e-9  # nH → H
            return 1.0 / (1j * 2 * math.pi * f * L)
        elif self.type == 'C':
            C = self.value * 1e-12  # pF → F
            return 1j * 2 * math.pi * f * C
        return 0j


class Wire:
    """두 핀을 연결하는 와이어"""
    def __init__(self, start_comp_id, start_pin_idx, end_comp_id, end_pin_idx):
        self.start_comp_id = start_comp_id   # 소자 id (또는 'port_plus'/'port_gnd')
        self.start_pin_idx = start_pin_idx   # 핀 인덱스 (0 or 1)
        self.end_comp_id = end_comp_id
        self.end_pin_idx = end_pin_idx

    def is_complete(self):
        return (self.start_comp_id is not None and self.end_comp_id is not None)


# ──────────────────────────────────────────────
# MNA 계산 스레드
# ──────────────────────────────────────────────

class MNAWorker(QObject):
    """Modified Nodal Analysis 백그라운드 계산"""
    progress = pyqtSignal(int)          # 0~100
    finished = pyqtSignal(object, object)  # freqs, Z_array
    error = pyqtSignal(str)

    def __init__(self, components, wires, port_plus_pin, port_gnd_pin, f_start, f_stop):
        super().__init__()
        self.components = components    # list of Component
        self.wires = wires              # list of Wire
        self.port_plus_pin = port_plus_pin  # (x, y) Port+ 핀 좌표
        self.port_gnd_pin = port_gnd_pin    # (x, y) PortGND 핀 좌표
        self.f_start = f_start          # Hz
        self.f_stop = f_stop            # Hz

    def run(self):
        try:
            # 주파수 배열 (0.01 MHz 스텝)
            f_start_mhz = self.f_start / 1e6
            f_stop_mhz = self.f_stop / 1e6
            n_points = int(round((f_stop_mhz - f_start_mhz) / 0.01)) + 1
            freqs_mhz = np.linspace(f_start_mhz, f_stop_mhz, n_points)
            freqs_hz = freqs_mhz * 1e6

            # 노드 식별 (Union-Find)
            nodes = self._build_nodes()
            if nodes is None:
                self.error.emit("회로 노드 구성 실패")
                return

            node_map, n_nodes, port_plus_node, gnd_node = nodes
            if port_plus_node is None:
                self.error.emit("Port+ 핀이 회로에 연결되지 않았습니다")
                return

            Z_array = np.zeros(len(freqs_hz), dtype=complex)

            for i, f in enumerate(freqs_hz):
                # 어드미턴스 행렬 Y 구성
                Y = np.zeros((n_nodes, n_nodes), dtype=complex)

                for comp in self.components:
                    y = comp.admittance(f)
                    pins = comp.get_pins()
                    # 핀의 노드 번호 찾기
                    key0 = self._pin_key(comp, 0)
                    key1 = self._pin_key(comp, 1)
                    n0 = node_map.get(key0)
                    n1 = node_map.get(key1)
                    if n0 is None or n1 is None:
                        continue
                    Y[n0, n0] += y
                    Y[n1, n1] += y
                    Y[n0, n1] -= y
                    Y[n1, n0] -= y

                # GND 노드를 기준(0)으로 제거
                rows = [k for k in range(n_nodes) if k != gnd_node]
                Y_red = Y[np.ix_(rows, rows)]

                # Port+ 노드 인덱스 (축소 행렬 기준)
                if port_plus_node < gnd_node:
                    pp_idx = port_plus_node
                else:
                    pp_idx = port_plus_node - 1

                # 전류 벡터 (Port+에 1A)
                I = np.zeros(len(rows), dtype=complex)
                I[pp_idx] = 1.0

                try:
                    V = np.linalg.solve(Y_red, I)
                    Z_array[i] = V[pp_idx]
                except np.linalg.LinAlgError:
                    Z_array[i] = complex(0, 0)

                if i % max(1, len(freqs_hz) // 100) == 0:
                    self.progress.emit(int(i / len(freqs_hz) * 100))

            self.progress.emit(100)
            self.finished.emit(freqs_mhz, Z_array)

        except Exception as e:
            self.error.emit(str(e))

    def _pin_key(self, comp, pin_idx):
        """소자 핀의 고유 키 반환"""
        return (comp.id, pin_idx)

    def _build_nodes(self):
        """
        Union-Find로 연결된 노드 그룹화.
        반환: (node_map, n_nodes, port_plus_node, gnd_node)
        node_map: { (comp_id, pin_idx) or 'port_plus'/'port_gnd' : node_number }
        """
        # 핀 키 집합 (모든 소자 핀 + 포트 핀)
        all_keys = []
        for comp in self.components:
            all_keys.append((comp.id, 0))
            all_keys.append((comp.id, 1))
        all_keys.append('port_plus')
        all_keys.append('port_gnd')

        parent = {k: k for k in all_keys}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # 와이어로 연결된 핀들 union
        for wire in self.wires:
            if not wire.is_complete():
                continue
            sk = self._wire_key(wire.start_comp_id, wire.start_pin_idx)
            ek = self._wire_key(wire.end_comp_id, wire.end_pin_idx)
            if sk in parent and ek in parent:
                union(sk, ek)

        # 고유 루트 → 노드 번호 매핑
        roots = list({find(k) for k in all_keys})
        root_to_num = {r: i for i, r in enumerate(roots)}
        node_map = {k: root_to_num[find(k)] for k in all_keys}

        n_nodes = len(roots)
        gnd_node = node_map.get('port_gnd')
        port_plus_node = node_map.get('port_plus')

        return node_map, n_nodes, port_plus_node, gnd_node

    def _wire_key(self, comp_id, pin_idx):
        """와이어 끝점 키 반환"""
        if comp_id in ('port_plus', 'port_gnd'):
            return comp_id
        return (comp_id, pin_idx)


# ──────────────────────────────────────────────
# MNA 단일 주파수 헬퍼 (민감도 분석용)
# ──────────────────────────────────────────────

def mna_solve_one(components, wires, f_hz):
    """단일 주파수 f_hz(Hz)에서 MNA로 입력 임피던스 Z 계산. 실패시 None 반환."""
    all_keys = []
    for comp in components:
        all_keys.append((comp.id, 0))
        all_keys.append((comp.id, 1))
    all_keys.append('port_plus')
    all_keys.append('port_gnd')

    parent = {k: k for k in all_keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def wkey(comp_id, pin_idx):
        if comp_id in ('port_plus', 'port_gnd'):
            return comp_id
        return (comp_id, pin_idx)

    for wire in wires:
        if not wire.is_complete():
            continue
        sk = wkey(wire.start_comp_id, wire.start_pin_idx)
        ek = wkey(wire.end_comp_id, wire.end_pin_idx)
        if sk in parent and ek in parent:
            union(sk, ek)

    roots = list({find(k) for k in all_keys})
    root_to_num = {r: i for i, r in enumerate(roots)}
    node_map = {k: root_to_num[find(k)] for k in all_keys}
    n_nodes = len(roots)
    gnd_node = node_map.get('port_gnd')
    port_plus_node = node_map.get('port_plus')

    if port_plus_node is None or gnd_node is None:
        return None

    Y = np.zeros((n_nodes, n_nodes), dtype=complex)
    for comp in components:
        y = comp.admittance(f_hz)
        n0 = node_map.get((comp.id, 0))
        n1 = node_map.get((comp.id, 1))
        if n0 is None or n1 is None:
            continue
        Y[n0, n0] += y
        Y[n1, n1] += y
        Y[n0, n1] -= y
        Y[n1, n0] -= y

    rows = [k for k in range(n_nodes) if k != gnd_node]
    Y_red = Y[np.ix_(rows, rows)]
    pp_idx = port_plus_node if port_plus_node < gnd_node else port_plus_node - 1
    I = np.zeros(len(rows), dtype=complex)
    I[pp_idx] = 1.0

    try:
        V = np.linalg.solve(Y_red, I)
        return V[pp_idx]
    except np.linalg.LinAlgError:
        return None


# ──────────────────────────────────────────────
# 회로 캔버스 (창1)
# ──────────────────────────────────────────────

SNAP = 10          # 스냅 그리드 px
GRID_VIS = 20      # 시각 그리드 px
PORT_X = 80        # Port 세로선 x 좌표


def snap(v):
    """값을 SNAP 그리드에 맞춤"""
    return round(v / SNAP) * SNAP


class CircuitCanvas(QWidget):
    """회로 설계 캔버스 위젯"""
    circuit_changed = pyqtSignal()  # 회로 변경 시그널

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        # 데이터
        self.components = []   # list[Component]
        self.wires = []        # list[Wire]

        # 소자 타입별 번호 카운터 (삭제해도 감소하지 않음)
        self._type_counters = {'R': 0, 'L': 0, 'C': 0}

        # 배치 모드
        self.place_mode = None       # 'R', 'L', 'C' or None
        self.ghost_pos = None        # (x, y) 고스트 위치

        # 선택 (단일)
        self.selected_comp = None

        # 복수 선택
        self.selected_components = set()   # 선택된 소자 id 집합

        # 드래그 이동 (단일)
        self.dragging = False
        self.drag_origin = None      # 드래그 시작 소자 위치

        # 복수 드래그 이동
        self.multi_dragging = False
        self.multi_drag_origin = None    # (x, y) 드래그 시작 마우스 위치

        # 와이어링 모드
        self.wiring = False
        self.wire_start = None       # (comp_id_or_port, pin_idx, x, y)
        self.wire_mouse_pos = None   # 현재 마우스 위치

        # 러버밴드 선택
        self.rubber_band_active = False
        self.rubber_band_origin = None   # (x, y) 드래그 시작점
        self.rubber_band_rect = None     # QRectF 선택 영역
        self._press_pos = None           # 마우스 Press 시작 위치

        # Port 위치 (캔버스 크기에 따라 동적 계산)
        self._update_port_positions()

    def _update_port_positions(self):
        """Port 핀 위치 계산"""
        h = self.height() if self.height() > 0 else 400
        cy = h // 2
        self.port_plus_y = snap(cy - 30)
        self.port_gnd_y = snap(cy + 30)

    def resizeEvent(self, event):
        self._update_port_positions()
        super().resizeEvent(event)

    def get_port_plus_pin(self):
        return (PORT_X, self.port_plus_y)

    def get_port_gnd_pin(self):
        return (PORT_X, self.port_gnd_y)

    # ── 배치 모드 진입 ──
    def enter_place_mode(self, comp_type):
        self.place_mode = comp_type
        self.selected_comp = None
        self.wiring = False
        self.wire_start = None
        self.setCursor(Qt.CrossCursor)
        self.update()

    # ── 유틸 ──
    def _comp_by_id(self, cid):
        for c in self.components:
            if c.id == cid:
                return c
        return None

    def _pin_pos(self, comp_id, pin_idx):
        """핀 좌표 반환"""
        if comp_id == 'port_plus':
            return self.get_port_plus_pin()
        if comp_id == 'port_gnd':
            return self.get_port_gnd_pin()
        comp = self._comp_by_id(comp_id)
        if comp:
            return comp.get_pins()[pin_idx]
        return None

    def _hit_pin(self, x, y, radius=10):
        """(x,y) 근처의 핀 반환 → (comp_id, pin_idx) or None"""
        # Port 핀 검사
        for port_id, pos in [('port_plus', self.get_port_plus_pin()),
                               ('port_gnd', self.get_port_gnd_pin())]:
            if math.hypot(x - pos[0], y - pos[1]) <= radius:
                return (port_id, 0)
        # 소자 핀 검사
        for comp in self.components:
            for i, pin in enumerate(comp.get_pins()):
                if math.hypot(x - pin[0], y - pin[1]) <= radius:
                    return (comp.id, i)
        return None

    def _hit_comp(self, x, y):
        """(x,y) 위치의 소자 반환"""
        for comp in reversed(self.components):
            if abs(x - comp.x) <= 35 and abs(y - comp.y) <= 20:
                return comp
        return None

    # ── 이벤트 ──
    def mousePressEvent(self, event):
        x, y = event.x(), event.y()
        sx, sy = snap(x), snap(y)

        if self.place_mode:
            # 배치 확정
            comp = Component(self.place_mode, sx, sy)
            self._type_counters[self.place_mode] += 1
            comp.name = f'{self.place_mode}{self._type_counters[self.place_mode]}'
            self.components.append(comp)
            self.place_mode = None
            self.ghost_pos = None
            self.setCursor(Qt.ArrowCursor)
            self.circuit_changed.emit()
            self.update()
            return

        # 핀 클릭 (와이어링)
        hit_pin = self._hit_pin(x, y)
        if hit_pin:
            if not self.wiring:
                # 와이어링 시작
                comp_id, pin_idx = hit_pin
                pos = self._pin_pos(comp_id, pin_idx)
                self.wiring = True
                self.wire_start = (comp_id, pin_idx, pos[0], pos[1])
                self.wire_mouse_pos = (x, y)
                self.update()
            else:
                # 와이어링 완료
                comp_id, pin_idx = hit_pin
                sc_id = self.wire_start[0]
                sp_idx = self.wire_start[1]
                if not (sc_id == comp_id and sp_idx == pin_idx):
                    wire = Wire(sc_id, sp_idx, comp_id, pin_idx)
                    self.wires.append(wire)
                    self.circuit_changed.emit()
                self.wiring = False
                self.wire_start = None
                self.wire_mouse_pos = None
                self.update()
            return

        shift = bool(event.modifiers() & Qt.ShiftModifier)

        # 소자 클릭
        comp = self._hit_comp(x, y)
        if comp:
            if self.wiring:
                self.wiring = False
                self.wire_start = None
            if shift:
                # Shift + 클릭: 기존 선택에 추가/제거 토글
                if comp.id in self.selected_components:
                    self.selected_components.discard(comp.id)
                    comp.selected = False
                    if self.selected_comp == comp:
                        self.selected_comp = None
                else:
                    self.selected_components.add(comp.id)
                    comp.selected = True
                    self.selected_comp = comp
            else:
                if comp.id in self.selected_components and len(self.selected_components) > 1:
                    # 이미 복수 선택된 소자 클릭 → 복수 드래그 시작
                    self.multi_dragging = True
                    self.multi_drag_origin = (x, y)
                elif self.selected_comp == comp:
                    # 단일 선택된 소자 재클릭 → 단일 드래그 시작
                    self.dragging = True
                    self.drag_origin = (comp.x, comp.y)
                else:
                    # 새 소자 단일 선택
                    for c in self.components:
                        c.selected = False
                    self.selected_components.clear()
                    comp.selected = True
                    self.selected_comp = comp
                    self.selected_components.add(comp.id)
            self._press_pos = (x, y)
            self.update()
            return

        # 빈 곳 클릭/드래그 → 러버밴드 시작 준비
        if self.wiring:
            self.wiring = False
            self.wire_start = None
        self._press_pos = (x, y)
        self.rubber_band_origin = (x, y)
        if not shift:
            # Shift 없으면 기존 선택 해제
            self.selected_comp = None
            self.selected_components.clear()
            for c in self.components:
                c.selected = False
        self.update()

    def mouseMoveEvent(self, event):
        x, y = event.x(), event.y()
        if self.place_mode:
            self.ghost_pos = (snap(x), snap(y))
            self.update()
        elif self.multi_dragging and self.multi_drag_origin:
            # 복수 소자 드래그 이동
            dx = snap(x) - snap(self.multi_drag_origin[0])
            dy = snap(y) - snap(self.multi_drag_origin[1])
            if dx != 0 or dy != 0:
                for cid in self.selected_components:
                    comp = self._comp_by_id(cid)
                    if comp:
                        comp.x += dx
                        comp.y += dy
                self.multi_drag_origin = (x, y)
                self.update()
        elif self.dragging and self.selected_comp:
            self.selected_comp.x = snap(x)
            self.selected_comp.y = snap(y)
            self.update()
        elif self.wiring:
            self.wire_mouse_pos = (x, y)
            self.update()
        elif self.rubber_band_origin and self._press_pos:
            # 러버밴드 시작 판정 (10px 이상 이동)
            px, py = self._press_pos
            if not self.rubber_band_active and math.hypot(x - px, y - py) >= 10:
                self.rubber_band_active = True
            if self.rubber_band_active:
                ox, oy = self.rubber_band_origin
                rx = min(ox, x)
                ry = min(oy, y)
                rw = abs(x - ox)
                rh = abs(y - oy)
                self.rubber_band_rect = QRectF(rx, ry, rw, rh)
                self.update()

    def mouseReleaseEvent(self, event):
        if self.multi_dragging:
            # 복수 드래그 종료: 스냅 정렬
            self.multi_dragging = False
            self.multi_drag_origin = None
            for cid in self.selected_components:
                comp = self._comp_by_id(cid)
                if comp:
                    comp.x = snap(comp.x)
                    comp.y = snap(comp.y)
            self.circuit_changed.emit()
            self.update()
        elif self.dragging:
            self.dragging = False
            self.circuit_changed.emit()

        if self.rubber_band_active and self.rubber_band_rect:
            # 러버밴드 선택 확정
            shift = bool(event.modifiers() & Qt.ShiftModifier)
            if not shift:
                for c in self.components:
                    c.selected = False
                self.selected_components.clear()
            for comp in self.components:
                bbox = QRectF(comp.x - 35, comp.y - 18, 70, 36)
                if self.rubber_band_rect.intersects(bbox):
                    self.selected_components.add(comp.id)
                    comp.selected = True
            # selected_comp 동기화
            if len(self.selected_components) == 1:
                cid = next(iter(self.selected_components))
                self.selected_comp = self._comp_by_id(cid)
            else:
                self.selected_comp = None
        elif self._press_pos and self.rubber_band_origin and not self.rubber_band_active:
            # 이동 < 10px: 빈 공간 클릭 → 전체 선택 해제 (Shift 없는 경우는 Press 시 이미 해제됨)
            pass

        # 러버밴드 상태 초기화
        self.rubber_band_active = False
        self.rubber_band_origin = None
        self.rubber_band_rect = None
        self._press_pos = None
        self.update()

    def mouseDoubleClickEvent(self, event):
        x, y = event.x(), event.y()
        comp = self._hit_comp(x, y)
        if comp:
            self._open_comp_dialog(comp)

    def _open_comp_dialog(self, comp):
        """소자 설정 커스텀 다이얼로그 (값 + 번호 수정)"""
        unit = {'R': 'Ω', 'L': 'nH', 'C': 'pF'}[comp.type]
        dlg = QDialog(self)
        dlg.setWindowTitle(f'{comp.name or comp.type} — 소자 설정')
        dlg.setFixedWidth(280)
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.setSpacing(10)
        dlg_layout.setContentsMargins(14, 14, 14, 14)

        # 번호 행
        num_row = QHBoxLayout()
        num_row.addWidget(QLabel('번호:'))
        num_row.addWidget(QLabel(comp.type))   # 접두어 (수정 불가)
        cur_num = comp.name[len(comp.type):] if comp.name and comp.name.startswith(comp.type) else '1'
        num_edit = QLineEdit(cur_num)
        num_edit.setFixedWidth(60)
        num_row.addWidget(num_edit)
        num_row.addStretch()
        dlg_layout.addLayout(num_row)

        # 값 행
        val_row = QHBoxLayout()
        val_row.addWidget(QLabel('값:'))
        val_edit = QLineEdit(str(comp.value) if comp.value is not None else '')
        val_edit.setValidator(QDoubleValidator(0, 1e15, 6))
        val_edit.setFixedWidth(100)
        val_row.addWidget(val_edit)
        val_row.addWidget(QLabel(unit))
        val_row.addStretch()
        dlg_layout.addLayout(val_row)

        # 버튼 행
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton('Cancel')
        ok_btn = QPushButton('OK')
        ok_btn.setDefault(True)
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        dlg_layout.addLayout(btn_row)

        if dlg.exec_() != QDialog.Accepted:
            return

        # 번호 적용 (중복 검사)
        new_num_str = num_edit.text().strip()
        if new_num_str:
            new_name = f'{comp.type}{new_num_str}'
            if new_name != comp.name:
                dup = any(c.name == new_name for c in self.components if c.id != comp.id)
                if dup:
                    QMessageBox.warning(self, '번호 중복',
                                        f'{new_name}은 이미 사용 중인 번호입니다')
                    return
                comp.name = new_name

        # 값 적용
        val_str = val_edit.text().strip()
        if val_str:
            try:
                comp.value = float(val_str)
                comp.error_highlight = False
                self.circuit_changed.emit()
                self.update()
            except ValueError:
                QMessageBox.warning(self, '입력 오류', '숫자를 입력하세요')

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self.place_mode = None
            self.ghost_pos = None
            self.wiring = False
            self.wire_start = None
            self.setCursor(Qt.ArrowCursor)
            self.update()
        elif key == Qt.Key_L and not self.selected_comp and not self.selected_components:
            # L 배치 단축키 (선택 없을 때)
            self.enter_place_mode('L')
            return
        elif key == Qt.Key_C and not self.selected_comp and not self.selected_components:
            # C 배치 단축키 (선택 없을 때)
            self.enter_place_mode('C')
            return
        elif key == Qt.Key_R and not self.selected_comp and not self.selected_components:
            # R 배치 단축키 (선택 없을 때)
            self.enter_place_mode('R')
            return
        elif key == Qt.Key_R and self.selected_components:
            # 선택된 모든 소자 90° 개별 회전
            for cid in self.selected_components:
                comp = self._comp_by_id(cid)
                if comp:
                    comp.horizontal = not comp.horizontal
            self.circuit_changed.emit()
            self.update()
        elif key == Qt.Key_Delete and self.selected_components:
            # 선택된 모든 소자 일괄 삭제
            ids_to_delete = set(self.selected_components)
            self.components = [c for c in self.components if c.id not in ids_to_delete]
            self.wires = [w for w in self.wires
                          if w.start_comp_id not in ids_to_delete
                          and w.end_comp_id not in ids_to_delete]
            self.selected_components.clear()
            self.selected_comp = None
            self.circuit_changed.emit()
            self.update()

    # ── 렌더링 ──
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 배경
        painter.fillRect(self.rect(), QColor('#F5F5F5'))

        # 그리드
        self._draw_grid(painter)

        # 와이어
        self._draw_wires(painter)

        # Port
        self._draw_port(painter)

        # 소자들
        for comp in self.components:
            self._draw_component(painter, comp)

        # 와이어링 임시선
        if self.wiring and self.wire_start and self.wire_mouse_pos:
            self._draw_temp_wire(painter)

        # 고스트
        if self.place_mode and self.ghost_pos:
            self._draw_ghost(painter)

        # 러버밴드 선택 사각형
        if self.rubber_band_active and self.rubber_band_rect:
            painter.setBrush(QBrush(QColor(0, 120, 215, 40)))
            painter.setPen(QPen(QColor(0, 120, 215, 180), 1))
            painter.drawRect(self.rubber_band_rect)

        painter.end()

    def _draw_grid(self, painter):
        """시각 그리드 (20px 점선)"""
        pen = QPen(QColor('#BDBDBD'), 1, Qt.DotLine)
        painter.setPen(pen)
        w, h = self.width(), self.height()
        for x in range(0, w, GRID_VIS):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, GRID_VIS):
            painter.drawLine(0, y, w, y)

    def _draw_port(self, painter):
        """Port 심볼 렌더링"""
        px = PORT_X
        py_plus = self.port_plus_y
        py_gnd = self.port_gnd_y

        # 세로선
        pen = QPen(QColor('#212121'), 2)
        painter.setPen(pen)
        painter.drawLine(px, py_plus, px, py_gnd)

        # 상단 핀 (+)
        painter.setBrush(QBrush(QColor('#4CAF50')))
        painter.drawEllipse(QRectF(px - 5, py_plus - 5, 10, 10))
        painter.setPen(QPen(QColor('#212121'), 1))
        font = QFont('Arial', 9)
        painter.setFont(font)
        painter.drawText(px + 8, py_plus + 5, '+')

        # 하단 핀 (GND)
        painter.setBrush(QBrush(QColor('#4CAF50')))
        painter.setPen(QPen(QColor('#4CAF50'), 1))
        painter.drawEllipse(QRectF(px - 5, py_gnd - 5, 10, 10))
        painter.setPen(QPen(QColor('#212121'), 1))
        painter.drawText(px + 8, py_gnd + 5, 'GND')

        # "Port" 텍스트
        painter.drawText(px - 45, (py_plus + py_gnd) // 2 + 5, 'Port')

    def _draw_component(self, painter, comp, opacity=1.0, override_pen=None):
        """소자 심볼 렌더링"""
        painter.save()
        painter.setOpacity(opacity)

        x, y = comp.x, comp.y

        # 색상 결정
        if override_pen:
            pen = override_pen
        elif comp.error_highlight:
            pen = QPen(QColor('#D32F2F'), 3)
            painter.fillRect(QRectF(x - 35, y - 18, 70, 36), QColor('#FFEBEE'))
        elif comp.selected:
            pen = QPen(QColor('#1565C0'), 3)
        else:
            pen = QPen(QColor('#212121'), 2)

        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if comp.horizontal:
            self._draw_symbol_h(painter, comp.type, x, y)
        else:
            self._draw_symbol_v(painter, comp.type, x, y)

        # 번호 레이블 (파란 이탤릭, 항상 수평)
        if comp.name:
            name_font = QFont('Arial', 9)
            name_font.setItalic(True)
            painter.setFont(name_font)
            painter.setPen(QPen(QColor('#0000FF'), 1))
            if comp.horizontal:
                # 수평 소자: 소자 중심 위, 수평 중앙 정렬
                painter.drawText(x - 15, y - 14, comp.name)
            else:
                # 수직 소자: 좌측 상단 (항상 수평 텍스트)
                painter.drawText(x - 28, y - 18, comp.name)

        # 값 레이블
        font = QFont('Arial', 9)
        painter.setFont(font)
        label_pen = QPen(QColor('#9E9E9E') if comp.value is None else QColor('#212121'), 1)
        painter.setPen(label_pen)
        label = comp.get_label()
        if comp.horizontal:
            painter.drawText(x - 20, y + 22, label)
        else:
            painter.drawText(x + 14, y + 5, label)

        painter.restore()

    def _draw_symbol_h(self, painter, comp_type, x, y):
        """수평 심볼 렌더링"""
        path = QPainterPath()
        if comp_type == 'R':
            path.moveTo(x - 30, y)
            path.lineTo(x - 20, y)
            path.lineTo(x - 15, y - 8)
            path.lineTo(x - 5, y + 8)
            path.lineTo(x + 5, y - 8)
            path.lineTo(x + 15, y + 8)
            path.lineTo(x + 20, y)
            path.lineTo(x + 30, y)
            painter.drawPath(path)
        elif comp_type == 'L':
            painter.drawLine(int(x - 30), int(y), int(x - 20), int(y))
            painter.drawLine(int(x + 20), int(y), int(x + 30), int(y))
            # 4개 반원 (위쪽 볼록)
            for i in range(4):
                cx = x - 20 + i * 10 + 5
                from PyQt5.QtCore import QRectF
                rect = QRectF(cx - 5, y - 5, 10, 10)
                painter.drawArc(rect, 0, 180 * 16)
        elif comp_type == 'C':
            painter.drawLine(int(x - 30), int(y), int(x - 4), int(y))
            painter.drawLine(int(x + 4), int(y), int(x + 30), int(y))
            old_pen = painter.pen()
            cap_pen = QPen(old_pen.color(), 3)
            painter.setPen(cap_pen)
            painter.drawLine(int(x - 4), int(y - 12), int(x - 4), int(y + 12))
            painter.drawLine(int(x + 4), int(y - 12), int(x + 4), int(y + 12))
            painter.setPen(old_pen)

    def _draw_symbol_v(self, painter, comp_type, x, y):
        """수직 심볼 렌더링 (90° 회전)"""
        path = QPainterPath()
        if comp_type == 'R':
            path.moveTo(x, y - 30)
            path.lineTo(x, y - 20)
            path.lineTo(x - 8, y - 15)
            path.lineTo(x + 8, y - 5)
            path.lineTo(x - 8, y + 5)
            path.lineTo(x + 8, y + 15)
            path.lineTo(x, y + 20)
            path.lineTo(x, y + 30)
            painter.drawPath(path)
        elif comp_type == 'L':
            painter.drawLine(int(x), int(y - 30), int(x), int(y - 20))
            painter.drawLine(int(x), int(y + 20), int(x), int(y + 30))
            for i in range(4):
                cy = y - 20 + i * 10 + 5
                rect = QRectF(x - 5, cy - 5, 10, 10)
                painter.drawArc(rect, 90 * 16, 180 * 16)
        elif comp_type == 'C':
            painter.drawLine(int(x), int(y - 30), int(x), int(y - 4))
            painter.drawLine(int(x), int(y + 4), int(x), int(y + 30))
            old_pen = painter.pen()
            cap_pen = QPen(old_pen.color(), 3)
            painter.setPen(cap_pen)
            painter.drawLine(int(x - 12), int(y - 4), int(x + 12), int(y - 4))
            painter.drawLine(int(x - 12), int(y + 4), int(x + 12), int(y + 4))
            painter.setPen(old_pen)

    def _draw_wires(self, painter):
        """모든 와이어 렌더링"""
        for wire in self.wires:
            if not wire.is_complete():
                continue
            start_pos = self._pin_pos(wire.start_comp_id, wire.start_pin_idx)
            end_pos = self._pin_pos(wire.end_comp_id, wire.end_pin_idx)
            if start_pos and end_pos:
                color = '#F44336'  # 연결 완료 = 빨간색
                pen = QPen(QColor(color), 2)
                painter.setPen(pen)
                # L자형 라우팅
                pts = self._l_route(start_pos, end_pos)
                for i in range(len(pts) - 1):
                    painter.drawLine(
                        int(pts[i][0]), int(pts[i][1]),
                        int(pts[i+1][0]), int(pts[i+1][1])
                    )

    def _l_route(self, p1, p2):
        """L자형 직각 라우팅 경유점 계산"""
        x1, y1 = p1
        x2, y2 = p2
        mid_x = snap((x1 + x2) // 2)
        # 수평 → 수직
        return [(x1, y1), (mid_x, y1), (mid_x, y2), (x2, y2)]

    def _draw_temp_wire(self, painter):
        """와이어링 중 임시 연결선"""
        sx, sy = self.wire_start[2], self.wire_start[3]
        mx, my = self.wire_mouse_pos
        pen = QPen(QColor('#212121'), 2, Qt.DashLine)
        painter.setPen(pen)
        pts = self._l_route((sx, sy), (mx, my))
        for i in range(len(pts) - 1):
            painter.drawLine(int(pts[i][0]), int(pts[i][1]),
                             int(pts[i+1][0]), int(pts[i+1][1]))

    def _draw_ghost(self, painter):
        """배치 모드 고스트 프리뷰"""
        gx, gy = self.ghost_pos
        painter.setOpacity(0.5)
        ghost_comp = Component(self.place_mode, gx, gy)
        ghost_pen = QPen(QColor('#212121'), 2)
        self._draw_symbol_h(painter, self.place_mode, gx, gy)
        painter.setOpacity(1.0)

    # ── 에러 검증 ──
    def validate_circuit(self):
        """
        계산 전 회로 유효성 검증.
        반환: (True, None) or (False, '오류메시지')
        """
        if not self.components:
            return False, 'Port에 아무 소자도 연결되지 않았습니다'

        # 연결된 소자 ID 집합
        connected_ids = set()
        for wire in self.wires:
            if wire.is_complete():
                if isinstance(wire.start_comp_id, int):
                    connected_ids.add(wire.start_comp_id)
                if isinstance(wire.end_comp_id, int):
                    connected_ids.add(wire.end_comp_id)

        # Port에 연결된 소자가 하나라도 있는지
        port_connected = any(
            w.start_comp_id in ('port_plus', 'port_gnd') or
            w.end_comp_id in ('port_plus', 'port_gnd')
            for w in self.wires if w.is_complete()
        )
        if not port_connected:
            return False, 'Port에 아무 소자도 연결되지 않았습니다'

        # 값 미입력 소자 검사
        no_value = []
        for comp in self.components:
            if comp.id in connected_ids and comp.value is None:
                comp.error_highlight = True
                no_value.append(comp)
            else:
                comp.error_highlight = False

        if no_value:
            self.update()
            return False, '값이 입력되지 않은 소자가 있습니다 (빨간색 표시)'

        # 미연결 핀 검사
        disconnected = []
        for comp in self.components:
            pins = comp.get_pins()
            for pin_idx in range(len(pins)):
                pin_connected = any(
                    (w.start_comp_id == comp.id and w.start_pin_idx == pin_idx) or
                    (w.end_comp_id == comp.id and w.end_pin_idx == pin_idx)
                    for w in self.wires if w.is_complete()
                )
                if not pin_connected:
                    comp.error_highlight = True
                    disconnected.append(comp)
                    break

        if disconnected:
            self.update()
            return False, '연결되지 않은 핀이 있는 소자가 존재합니다 (빨간색 표시)'

        return True, None


# ──────────────────────────────────────────────
# Smith Chart 위젯 (창2)
# ──────────────────────────────────────────────

Z0 = 50.0  # 기준 임피던스

class SmithChartWidget(QWidget):
    """Smith Chart 표시 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.fig = Figure(figsize=(5, 5), facecolor='white')
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setParent(self)
        layout.addWidget(self.canvas)

        self.ax = self.fig.add_subplot(111, aspect='equal')
        self._draw_smith_background()

        # 데이터
        self.freqs = None    # MHz 배열
        self.Z_data = None   # 복소 임피던스 배열
        self.user_marker = None   # 사용자 마커 아티스트

        # 클릭 이벤트
        self.canvas.mpl_connect('button_press_event', self._on_click)

    def _draw_smith_background(self):
        """Smith Chart 배경 그리기 (등저항원, 등리액턴스호)"""
        ax = self.ax
        ax.clear()
        ax.set_aspect('equal')
        ax.set_xlim(-1.1, 1.1)
        ax.set_ylim(-1.1, 1.1)
        ax.axis('off')

        gray = '#CCCCCC'
        lw = 0.7

        # 단위원
        theta = np.linspace(0, 2 * np.pi, 500)
        ax.plot(np.cos(theta), np.sin(theta), 'k-', lw=1.2, zorder=2)

        # 실수 축
        ax.axhline(0, color=gray, lw=lw, zorder=1)

        # 등저항 원: r = 0, 0.2, 0.5, 1, 2, 5
        for r in [0, 0.2, 0.5, 1, 2, 5]:
            cx = r / (1 + r)
            radius = 1 / (1 + r)
            circle = plt.Circle((cx, 0), radius, fill=False, color=gray, lw=lw, zorder=1)
            ax.add_patch(circle)

        # 등리액턴스 호: x = ±0.2, ±0.5, ±1, ±2, ±5
        for x in [0.2, 0.5, 1, 2, 5]:
            for sign in [1, -1]:
                xv = sign * x
                cx = 1.0
                cy = 1.0 / xv
                radius = abs(1.0 / xv)
                # 단위원 내부 호만 그리기
                t = np.linspace(0, 2 * np.pi, 1000)
                px = cx + radius * np.cos(t)
                py = cy + radius * np.sin(t)
                mask = px ** 2 + py ** 2 <= 1.001
                # 마스크된 세그먼트 그리기
                in_circle = mask
                segs_x, segs_y = [], []
                cur_x, cur_y = [], []
                for i in range(len(px)):
                    if in_circle[i]:
                        cur_x.append(px[i])
                        cur_y.append(py[i])
                    else:
                        if cur_x:
                            ax.plot(cur_x, cur_y, color=gray, lw=lw, zorder=1)
                            cur_x, cur_y = [], []
                if cur_x:
                    ax.plot(cur_x, cur_y, color=gray, lw=lw, zorder=1)

        # 축 레이블
        ax.text(1.05, 0, '∞', ha='left', va='center', fontsize=8, color='#555555')
        ax.text(-1.05, 0, '0', ha='right', va='center', fontsize=8, color='#555555')
        ax.text(0.02, 0.02, '1', ha='left', va='bottom', fontsize=8, color='#555555')

        self.canvas.draw()

    def update_data(self, freqs_mhz, Z_array):
        """계산 결과 업데이트 및 플로팅"""
        self.freqs = freqs_mhz
        self.Z_data = Z_array

        self._draw_smith_background()
        ax = self.ax

        # 반사계수 계산
        gamma = (Z_array - Z0) / (Z_array + Z0)
        gx = gamma.real
        gy = gamma.imag

        # 궤적 플로팅
        ax.plot(gx, gy, color='#1E88E5', lw=2, zorder=3)

        # 시작/끝 마커
        ax.plot(gx[0], gy[0], 's', color='#1E88E5', markersize=8, zorder=5)
        ax.annotate(f'{freqs_mhz[0]:.4g}MHz', (gx[0], gy[0]),
                    textcoords='offset points', xytext=(5, 5), fontsize=8)

        ax.plot(gx[-1], gy[-1], 's', color='#F44336', markersize=8, zorder=5)
        ax.annotate(f'{freqs_mhz[-1]:.4g}MHz', (gx[-1], gy[-1]),
                    textcoords='offset points', xytext=(5, -12), fontsize=8)

        self.canvas.draw()

    def _on_click(self, event):
        """Smith Chart 클릭 → 주파수 팝업 → 사용자 마커"""
        if self.freqs is None or self.Z_data is None:
            return
        if event.inaxes != self.ax:
            return

        cx, cy = event.xdata, event.ydata
        gamma = (self.Z_data - Z0) / (self.Z_data + Z0)
        gx = gamma.real
        gy = gamma.imag

        # 가장 가까운 주파수 포인트 (default 값)
        dist = (gx - cx) ** 2 + (gy - cy) ** 2
        idx_default = np.argmin(dist)
        f_default = self.freqs[idx_default]

        # ★ 주파수 입력 팝업 (QInputDialog)
        f_str, ok = QInputDialog.getText(
            self.parent(), '마커 주파수 설정',
            '주파수 (MHz):',
            text=f'{f_default:.4f}'
        )
        if not ok or not f_str.strip():
            return
        try:
            f_input = float(f_str.strip())
        except ValueError:
            return

        # 입력된 주파수에 가장 가까운 데이터 포인트 찾기
        idx = np.argmin(np.abs(self.freqs - f_input))
        f = self.freqs[idx]
        Z = self.Z_data[idx]
        g = gamma[idx]

        # 기존 마커 제거
        if self.user_marker:
            for art in self.user_marker:
                try:
                    art.remove()
                except Exception:
                    pass

        # 마커 생성
        marker_pt, = self.ax.plot(gx[idx], gy[idx], 'o', color='#FF9800',
                                   markersize=8, zorder=6)
        label_txt = self.ax.annotate(
            f'f={f:.2f}MHz\nZ={Z.real:.1f}+j{Z.imag:.1f}Ω\nΓ={g.real:.3f}+j{g.imag:.3f}',
            (gx[idx], gy[idx]), textcoords='offset points', xytext=(8, 8),
            fontsize=7, bbox=dict(boxstyle='round,pad=0.3', fc='white', alpha=0.8)
        )
        self.user_marker = [marker_pt, label_txt]
        self.canvas.draw()


# ──────────────────────────────────────────────
# Advanced 윈도우 (윈도우2)
# ──────────────────────────────────────────────

class AdvancedWindow(QDialog):
    """Advanced - Impedance Analysis 창"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Advanced - Impedance Analysis')
        self.setWindowFlags(
            (self.windowFlags() | Qt.Window)
            & ~Qt.WindowContextHelpButtonHint
        )
        self.resize(800, 600)
        self.setMinimumSize(600, 400)

        # 데이터
        self.freqs = None
        self.Z_data = None

        # 커서 라인
        self.cursor_line1 = None
        self.cursor_line2 = None
        self.cursor_marker1 = None
        self.cursor_marker2 = None
        self.cursor_label1 = None
        self.cursor_label2 = None

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 툴바
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel('Y Scale:'))
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(['Linear', 'Logarithmic'])
        self.scale_combo.currentIndexChanged.connect(self._on_scale_changed)
        toolbar.addWidget(self.scale_combo)
        toolbar.addSpacing(20)
        toolbar.addWidget(QLabel('Mag Unit:'))
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(['Ω', 'dB'])
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        toolbar.addWidget(self.unit_combo)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 그래프
        self.fig = Figure(facecolor='white')
        self.canvas = FigureCanvas(self.fig)
        self.ax_mag = self.fig.add_subplot(211)
        self.ax_phase = self.fig.add_subplot(212)
        self._init_axes()
        layout.addWidget(self.canvas, stretch=1)

        # 상태바
        self.status_label = QLabel('Cursor: —')
        self.status_label.setStyleSheet('padding: 2px 6px; border-top: 1px solid #CCCCCC;')
        layout.addWidget(self.status_label)

        # 클릭 이벤트
        self.canvas.mpl_connect('button_press_event', self._on_click)

    def _init_axes(self):
        """초기 축 설정"""
        for ax, ylabel, color in [
            (self.ax_mag, 'Magnitude (Ω)', '#1E88E5'),
            (self.ax_phase, 'Phase (°)', '#E53935')
        ]:
            ax.set_facecolor('white')
            ax.grid(True, color='#E0E0E0', linestyle='--')
            ax.set_xlabel('Frequency (MHz)')
            ax.set_ylabel(ylabel)

        self.ax_phase.set_ylim(-180, 180)
        self.ax_phase.set_yticks([-180, -135, -90, -45, 0, 45, 90, 135, 180])

        # "No data" 텍스트
        for ax in [self.ax_mag, self.ax_phase]:
            ax.text(0.5, 0.5, 'No data - Press Calculate',
                    transform=ax.transAxes, ha='center', va='center',
                    fontsize=11, color='#888888')

        self.fig.tight_layout()
        self.canvas.draw()

    def update_data(self, freqs_mhz, Z_array):
        """계산 결과 업데이트"""
        self.freqs = freqs_mhz
        self.Z_data = Z_array
        self._replot()

    def _replot(self):
        if self.freqs is None or self.Z_data is None:
            return

        freqs = self.freqs
        Z = self.Z_data
        mag_unit = self.unit_combo.currentText()
        scale = self.scale_combo.currentText()

        # Magnitude
        mag = np.abs(Z)
        if mag_unit == 'dB':
            with np.errstate(divide='ignore'):
                y_mag = 20 * np.log10(mag)
        else:
            y_mag = mag

        # Phase
        phase = np.angle(Z, deg=True)

        self.ax_mag.clear()
        self.ax_phase.clear()

        # Magnitude 플롯
        self.ax_mag.plot(freqs, y_mag, color='#1E88E5', lw=1.5)
        self.ax_mag.set_facecolor('white')
        self.ax_mag.grid(True, color='#E0E0E0', linestyle='--')
        self.ax_mag.set_xlabel('Frequency (MHz)')
        ylabel = f'Magnitude ({mag_unit})'
        self.ax_mag.set_ylabel(ylabel)
        # Y축 Scale: Magnitude에만 적용 (X축은 항상 Linear 고정)
        yscale = 'log' if scale == 'Logarithmic' else 'linear'
        self.ax_mag.set_xscale('linear')
        self.ax_mag.set_yscale(yscale)

        # Phase 플롯
        self.ax_phase.plot(freqs, phase, color='#E53935', lw=1.5)
        self.ax_phase.set_facecolor('white')
        self.ax_phase.grid(True, color='#E0E0E0', linestyle='--')
        self.ax_phase.set_xlabel('Frequency (MHz)')
        self.ax_phase.set_ylabel('Phase (°)')
        self.ax_phase.set_ylim(-180, 180)
        self.ax_phase.set_yticks([-180, -135, -90, -45, 0, 45, 90, 135, 180])
        # Phase X축도 항상 Linear, Y축도 항상 Linear 고정
        self.ax_phase.set_xscale('linear')

        # 커서 초기화
        self.cursor_line1 = None
        self.cursor_line2 = None

        self.fig.tight_layout()
        self.canvas.draw()

    def _on_scale_changed(self):
        self._replot()

    def _on_unit_changed(self):
        self._replot()

    def _on_click(self, event):
        """커서라인 처리"""
        if self.freqs is None or self.Z_data is None:
            return
        if event.inaxes not in [self.ax_mag, self.ax_phase]:
            return

        f_click = event.xdata
        if f_click is None:
            return

        # 가장 가까운 주파수 인덱스
        idx = np.argmin(np.abs(self.freqs - f_click))
        f = self.freqs[idx]
        Z = self.Z_data[idx]
        mag = abs(Z)
        phase = math.degrees(math.atan2(Z.imag, Z.real))
        gamma = (Z - Z0) / (Z + Z0)

        mag_unit = self.unit_combo.currentText()
        if mag_unit == 'dB':
            mag_disp = f'{20 * math.log10(mag):.1f}dB' if mag > 0 else '-∞dB'
        else:
            mag_disp = f'{mag:.1f}Ω'

        # 커서 라인 갱신
        for ax, attr in [(self.ax_mag, 'cursor_line1'), (self.ax_phase, 'cursor_line2')]:
            line = getattr(self, attr)
            if line:
                try:
                    line.remove()
                except Exception:
                    pass
            new_line = ax.axvline(f, color='#FF9800', lw=1, zorder=5)
            setattr(self, attr, new_line)

        # 마커 및 레이블 갱신
        mag_val = abs(Z)
        if mag_unit == 'dB':
            y_mag_val = 20 * math.log10(mag_val) if mag_val > 0 else -999
        else:
            y_mag_val = mag_val

        for ax, attr_m, attr_l, yval, ytxt in [
            (self.ax_mag, 'cursor_marker1', 'cursor_label1',
             y_mag_val, mag_disp),
            (self.ax_phase, 'cursor_marker2', 'cursor_label2',
             phase, f'{phase:+.1f}°')
        ]:
            for attr in [attr_m, attr_l]:
                art = getattr(self, attr)
                if art:
                    try:
                        art.remove()
                    except Exception:
                        pass
            m, = ax.plot(f, yval, 'o', color='#FF9800', markersize=6, zorder=6)
            l = ax.annotate(ytxt, (f, yval), textcoords='offset points',
                            xytext=(5, 5), fontsize=9,
                            bbox=dict(boxstyle='round,pad=0.2', fc='white', alpha=0.8))
            setattr(self, attr_m, m)
            setattr(self, attr_l, l)

        self.canvas.draw()

        # 상태바 업데이트
        self.status_label.setText(
            f'Cursor: f={f:.2f}MHz  |Z|={mag_disp}  ∠Z={phase:+.1f}°  '
            f'Γ={gamma.real:.3f}+j{gamma.imag:.3f}'
        )


# ──────────────────────────────────────────────
# 민감도 분석 워커
# ──────────────────────────────────────────────

class SensitivityWorker(QObject):
    """수치 미분으로 각 소자의 임피던스 민감도 계산"""
    finished = pyqtSignal(float, object)   # z_nominal_abs, list of dict
    error = pyqtSignal(str)

    def __init__(self, components, wires, f_hz):
        super().__init__()
        import copy
        self.components = copy.deepcopy(components)
        self.wires = wires
        self.f_hz = f_hz
        self.eps = 0.01   # 고정 상대 섭동량 (1%, 수치 미분 안정성 확보)

    def run(self):
        try:
            Z0 = mna_solve_one(self.components, self.wires, self.f_hz)
            if Z0 is None or abs(Z0) == 0:
                self.error.emit('기준 임피던스 계산 실패 — Port가 올바르게 연결됐는지 확인하세요')
                return

            # 타입별 일련번호
            type_idx = {}
            results = []

            for comp in self.components:
                if comp.value is None or comp.value == 0:
                    continue
                t = comp.type
                type_idx[t] = type_idx.get(t, 0) + 1
                name = comp.name if comp.name else f'{t}{type_idx[t]}'
                unit = {'R': 'Ω', 'L': 'nH', 'C': 'pF'}[t]
                xi = comp.value

                # 중앙 차분: xi*(1±eps)
                comp.value = xi * (1.0 + self.eps)
                Z_plus = mna_solve_one(self.components, self.wires, self.f_hz)
                comp.value = xi * (1.0 - self.eps)
                Z_minus = mna_solve_one(self.components, self.wires, self.f_hz)
                comp.value = xi  # 복원

                if Z_plus is None or Z_minus is None:
                    continue

                # |Z| 기반 민감도: L/C는 순허수라 .real 취하면 0이 되므로 크기 기반으로 계산
                abs_Z_plus  = abs(Z_plus)
                abs_Z_minus = abs(Z_minus)
                abs_Z0      = abs(Z0)

                # 절대 민감도: Δ|Z| / ΔXi  (단위: Ω/unit, 부호 보존)
                abs_sens = (abs_Z_plus - abs_Z_minus) / (2.0 * xi * self.eps)
                # 정규화 민감도: (Δ|Z|/|Z|) / (ΔXi/Xi)  (무차원, 부호 보존)
                norm_sens = abs_sens * (xi / abs_Z0) if abs_Z0 != 0 else 0.0

                # 값 표시 문자열 (정수면 정수형, 소수면 소수형)
                if xi == int(xi):
                    val_str = f'{int(xi)} {unit}'
                else:
                    val_str = f'{xi} {unit}'

                results.append({
                    'name': name,
                    'type': t,
                    'value': xi,
                    'unit': unit,
                    'val_str': val_str,
                    'abs_sens': abs_sens,
                    'norm_sens': norm_sens,
                })

            if not results:
                self.error.emit('민감도를 계산할 소자가 없습니다')
                return

            # 랭킹: |norm_sens| 내림차순
            results.sort(key=lambda r: -abs(r['norm_sens']))
            for i, r in enumerate(results):
                r['rank'] = i + 1

            self.finished.emit(abs(Z0), results)

        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# 민감도 결과 윈도우
# ──────────────────────────────────────────────

class SensitivityWindow(QDialog):
    """Sensitivity Analysis Results 창"""

    def __init__(self, f_mhz, z_nominal, results, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Sensitivity Analysis Results')
        self.setWindowFlags(
            (self.windowFlags() | Qt.Window)
            & ~Qt.WindowContextHelpButtonHint
        )
        self.resize(1020, 480)
        self.setMinimumSize(760, 380)
        self.f_mhz = f_mhz
        self.z_nominal = z_nominal   # |Z_nominal| in Ω
        self.results = results
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── 헤더 ──
        n = len(self.results)
        header = QLabel(
            f'분석 주파수: {self.f_mhz:.3f} MHz   |   '
            f'Z_nominal: {self.z_nominal:.2f} Ω   |   소자 수: {n}개'
        )
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        header.setFont(font)
        header.setStyleSheet('padding: 4px 2px;')
        layout.addWidget(header)

        # ── 본문 (좌: 테이블 / 우: 토네이도 차트) ──
        content = QHBoxLayout()
        content.setSpacing(10)

        # ── 좌: 테이블 ──
        table = QTableWidget(n, 5)
        table.setHorizontalHeaderLabels(
            ['Component', 'Value', 'ΔZ/ΔXi', 'Norm. S', 'Rank']
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        table.setSelectionBehavior(QTableWidget.SelectItems)
        table.verticalHeader().setVisible(True)
        table.setAlternatingRowColors(False)

        # 우클릭 컨텍스트 메뉴 (클립보드 복사)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos: self._show_table_context_menu(table, pos)
        )

        sorted_r = sorted(self.results, key=lambda r: r['rank'])

        # 그라데이션 하이라이트 설정: 소자 >5개이면 상위 5개, 이하이면 상위 3개
        highlight_count = 5 if n > 5 else 3
        max_abs = abs(sorted_r[0]['norm_sens']) if sorted_r else 1.0
        if max_abs == 0:
            max_abs = 1.0

        for row_idx, r in enumerate(sorted_r):
            texts = [
                r['name'],
                r['val_str'],
                f"{r['abs_sens']:.3f}",
                f"{r['norm_sens']:.4f}",
                str(r['rank']),
            ]
            # 그라데이션 배경 계산
            alpha = abs(r['norm_sens']) / max_abs if r['rank'] <= highlight_count else 0.0
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                # 상위 N개 행: |Norm.S| 비례 그라데이션 배경
                if r['rank'] <= highlight_count:
                    g_val = 255 - round(alpha * 30)
                    b_val = 255 - round(alpha * 37)
                    item.setBackground(QColor(255, g_val, b_val))
                table.setItem(row_idx, col, item)

        content.addWidget(table, stretch=45)

        # ── 우: 토네이도 차트 ──
        fig = Figure(facecolor='white')
        canvas = FigureCanvas(fig)
        ax = fig.add_subplot(111)

        # rank 순으로 정렬 (rank1=top)
        n = len(sorted_r)
        y_pos = list(range(n - 1, -1, -1))   # [n-1, ..., 0]
        names    = [r['name']     for r in sorted_r]
        norm_v   = [r['norm_sens'] for r in sorted_r]
        abs_v    = [r['abs_sens']  for r in sorted_r]

        # 파란 수평 막대 (정규화 민감도)
        bars = ax.barh(y_pos, norm_v, color='#5C9BD6', height=0.5, zorder=3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(names, fontsize=9)
        ax.set_xlabel('Normalized Sensitivity (ΔZ/Z per ΔXi/Xi)', fontsize=9)
        ax.grid(True, axis='x', color='#E0E0E0', linestyle='--', zorder=1)
        ax.set_facecolor('white')

        # 막대 내부/측 S= 레이블
        x_range = max(abs(v) for v in norm_v) if norm_v else 1
        for yi, nv in zip(y_pos, norm_v):
            lbl = f'S={nv:.3f}'
            inside = abs(nv) > x_range * 0.25
            if nv < 0:
                xpos = nv / 2 if inside else nv - x_range * 0.02
                ha = 'center' if inside else 'right'
            else:
                xpos = nv / 2 if inside else nv + x_range * 0.02
                ha = 'center' if inside else 'left'
            color = 'white' if inside else '#333333'
            ax.text(xpos, yi, lbl, va='center', ha=ha,
                    fontsize=8, color=color, zorder=5)

        # 상단 x축 (절대 민감도, 오렌지)
        ax2 = ax.twiny()
        ax2.set_xlabel('Absolute Sensitivity |ΔZ/ΔXi| (Ω/unit)', color='#FF9800', fontsize=9)
        ax2.tick_params(axis='x', colors='#FF9800', labelsize=8)
        ax2.spines['top'].set_edgecolor('#FF9800')
        # 오렌지 다이아몬드
        ax2.plot(abs_v, y_pos, 'D', color='#FF9800', markersize=8, zorder=6)

        ax.set_title(
            f'Tornado Chart — Local Sensitivity @ {self.f_mhz:.3f} MHz',
            fontsize=10, pad=8
        )

        fig.tight_layout()
        content.addWidget(canvas, stretch=55)

        layout.addLayout(content)

    def _show_table_context_menu(self, table, pos):
        """테이블 우클릭 메뉴"""
        menu = QMenu(self)
        copy_action = menu.addAction('클립보드에 복사')
        action = menu.exec_(table.viewport().mapToGlobal(pos))
        if action == copy_action:
            self._copy_selection_to_clipboard(table)

    def _copy_selection_to_clipboard(self, table):
        """선택된 셀을 탭 구분 텍스트(TSV)로 클립보드에 복사"""
        selection = table.selectedRanges()
        if not selection:
            return

        rows = set()
        cols = set()
        for sel_range in selection:
            for r in range(sel_range.topRow(), sel_range.bottomRow() + 1):
                rows.add(r)
            for c in range(sel_range.leftColumn(), sel_range.rightColumn() + 1):
                cols.add(c)

        rows = sorted(rows)
        cols = sorted(cols)

        # 헤더 포함
        header_texts = []
        for c in cols:
            header_item = table.horizontalHeaderItem(c)
            header_texts.append(header_item.text() if header_item else '')
        lines = ['\t'.join(header_texts)]

        # 데이터 행
        for r in rows:
            row_texts = []
            for c in cols:
                item = table.item(r, c)
                row_texts.append(item.text() if item else '')
            lines.append('\t'.join(row_texts))

        text = '\n'.join(lines)
        QApplication.clipboard().setText(text)


# ──────────────────────────────────────────────
# 메인 윈도우
# ──────────────────────────────────────────────

class MainWindow(QMainWindow):
    """메인 윈도우"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle('RF Circuit Design - Smith Chart Analyzer')
        self.resize(1600, 800)
        self.setMinimumSize(1200, 600)

        # 계산 결과 저장
        self.last_freqs = None
        self.last_Z = None
        self.advanced_win = None

        # 스레드
        self.calc_thread = None
        self.calc_worker = None

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ── 상단 툴바 ──
        toolbar_widget = QWidget()
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(2, 2, 2, 2)
        toolbar_layout.setSpacing(6)

        # R, L, C 버튼
        for name in ['R', 'L', 'C']:
            btn = QPushButton(name)
            btn.setFixedSize(36, 28)
            btn.clicked.connect(lambda checked, n=name: self.circuit_canvas.enter_place_mode(n))
            toolbar_layout.addWidget(btn)

        toolbar_layout.addSpacing(10)

        # 주파수 입력
        toolbar_layout.addWidget(QLabel('시작(MHz):'))
        self.freq_start = QLineEdit('1')
        self.freq_start.setFixedWidth(70)
        self.freq_start.setValidator(QDoubleValidator(0.001, 1e6, 4))
        toolbar_layout.addWidget(self.freq_start)

        toolbar_layout.addWidget(QLabel('끝(MHz):'))
        self.freq_stop = QLineEdit('100')
        self.freq_stop.setFixedWidth(70)
        self.freq_stop.setValidator(QDoubleValidator(0.001, 1e6, 4))
        toolbar_layout.addWidget(self.freq_stop)

        # Cal 버튼
        self.cal_btn = QPushButton('Cal')
        self.cal_btn.setFixedSize(50, 28)
        self.cal_btn.clicked.connect(self._on_calculate)
        toolbar_layout.addWidget(self.cal_btn)

        # Save 버튼
        self.save_btn = QPushButton('Save')
        self.save_btn.setFixedSize(50, 28)
        self.save_btn.clicked.connect(self._on_save)
        toolbar_layout.addWidget(self.save_btn)

        # Load 버튼
        self.load_btn = QPushButton('Load')
        self.load_btn.setFixedSize(50, 28)
        self.load_btn.clicked.connect(self._on_load)
        toolbar_layout.addWidget(self.load_btn)

        toolbar_layout.addStretch()

        main_layout.addWidget(toolbar_widget)

        # ── 본문: 분할 패널 ──
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # 창1: 회로 설계 (래퍼 위젯으로 감싸기)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)

        # Sensitivity Analysis 버튼 (창1 우측 상단, Advanced 버튼과 동일 높이)
        sens_row = QHBoxLayout()
        sens_row.addStretch()
        self.sens_btn = QPushButton('Sensitivity Analysis')
        self.sens_btn.setFixedHeight(28)    # Advanced 버튼과 동일 높이
        self.sens_btn.setStyleSheet(
            'background-color: #4A90D9; color: white; font-size: 10pt; border-radius: 3px;'
        )
        self.sens_btn.clicked.connect(self._open_sensitivity)
        sens_row.addWidget(self.sens_btn)
        left_layout.addLayout(sens_row)

        self.circuit_canvas = CircuitCanvas()
        left_layout.addWidget(self.circuit_canvas, stretch=1)
        splitter.addWidget(left_widget)

        # 창2: Smith Chart + Advanced 버튼
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 4, 0)
        right_layout.setSpacing(2)

        # Advanced 버튼 (우측 상단)
        adv_row = QHBoxLayout()
        adv_row.addStretch()
        self.adv_btn = QPushButton('Advanced')
        self.adv_btn.setFixedSize(80, 28)
        self.adv_btn.setStyleSheet(
            'background-color: #4A90D9; color: white; font-size: 10pt; border-radius: 3px;'
        )
        self.adv_btn.clicked.connect(self._open_advanced)
        adv_row.addWidget(self.adv_btn)
        right_layout.addLayout(adv_row)

        self.smith_widget = SmithChartWidget()
        right_layout.addWidget(self.smith_widget, stretch=1)

        splitter.addWidget(right_widget)

        # 60:40 비율
        total_w = 1600
        splitter.setSizes([int(total_w * 0.6), int(total_w * 0.4)])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter, stretch=1)

        # 상태바
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage('준비')

    def _on_save(self):
        """회로를 JSON 파일로 저장"""
        path, _ = QFileDialog.getSaveFileName(
            self, '회로 저장', '', 'JSON Files (*.json);;All Files (*)'
        )
        if not path:
            return
        data = {
            'version': 1,
            'freq_start_mhz': self.freq_start.text(),
            'freq_stop_mhz': self.freq_stop.text(),
            'type_counters': self.circuit_canvas._type_counters.copy(),
            'components': [
                {
                    'id': c.id,
                    'type': c.type,
                    'name': c.name,
                    'x': c.x,
                    'y': c.y,
                    'value': c.value,
                    'horizontal': c.horizontal,
                }
                for c in self.circuit_canvas.components
            ],
            'wires': [
                {
                    'start_comp_id': w.start_comp_id,
                    'start_pin_idx': w.start_pin_idx,
                    'end_comp_id': w.end_comp_id,
                    'end_pin_idx': w.end_pin_idx,
                }
                for w in self.circuit_canvas.wires
            ],
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f'저장 완료: {path}')
        except Exception as e:
            QMessageBox.warning(self, '저장 오류', f'저장 중 오류 발생:\n{e}')

    def _on_load(self):
        """JSON 파일에서 회로 불러오기"""
        path, _ = QFileDialog.getOpenFileName(
            self, '회로 불러오기', '', 'JSON Files (*.json);;All Files (*)'
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, '불러오기 오류', f'파일 읽기 오류:\n{e}')
            return

        # 기존 데이터 초기화
        self.circuit_canvas.components.clear()
        self.circuit_canvas.wires.clear()
        self.circuit_canvas.selected_comp = None
        self.circuit_canvas.selected_components.clear()

        # 주파수 복원
        self.freq_start.setText(data.get('freq_start_mhz', '1'))
        self.freq_stop.setText(data.get('freq_stop_mhz', '100'))

        # 타입 카운터 복원
        self.circuit_canvas._type_counters = data.get(
            'type_counters', {'R': 0, 'L': 0, 'C': 0}
        )

        # 소자 복원
        for cd in data.get('components', []):
            comp = Component(cd['type'], cd['x'], cd['y'])
            comp.id = cd['id']
            comp.name = cd.get('name')
            comp.value = cd.get('value')
            comp.horizontal = cd.get('horizontal', True)
            self.circuit_canvas.components.append(comp)

        # Component._id_counter 복원 (최대 id + 1)
        if self.circuit_canvas.components:
            Component._id_counter = max(c.id for c in self.circuit_canvas.components)

        # 와이어 복원 (start_comp_id가 정수면 int로 변환)
        for wd in data.get('wires', []):
            s_id = wd['start_comp_id']
            e_id = wd['end_comp_id']
            if isinstance(s_id, str) and s_id.lstrip('-').isdigit():
                s_id = int(s_id)
            if isinstance(e_id, str) and e_id.lstrip('-').isdigit():
                e_id = int(e_id)
            wire = Wire(s_id, wd['start_pin_idx'], e_id, wd['end_pin_idx'])
            self.circuit_canvas.wires.append(wire)

        # Cal 결과 초기화 (저장되지 않으므로)
        self.last_freqs = None
        self.last_Z = None
        self.smith_widget._draw_smith_background()

        self.circuit_canvas.update()
        self.statusBar().showMessage(f'불러오기 완료: {path}')

    def _open_advanced(self):
        """Advanced 윈도우 열기"""
        if self.advanced_win is None or not self.advanced_win.isVisible():
            self.advanced_win = AdvancedWindow(self)
            if self.last_freqs is not None:
                self.advanced_win.update_data(self.last_freqs, self.last_Z)
            self.advanced_win.show()
        else:
            self.advanced_win.raise_()
            self.advanced_win.activateWindow()

    def _open_sensitivity(self):
        """Sensitivity Analysis 버튼 처리"""
        # 회로 유효성 검증 (Cal 결과 불필요)
        valid, err = self.circuit_canvas.validate_circuit()
        if not valid:
            QMessageBox.warning(self, '회로 오류', err)
            return

        # 소자 수 검증
        connected_comps = [c for c in self.circuit_canvas.components if c.value is not None]
        if len(connected_comps) < 2:
            QMessageBox.warning(self, '안내', '민감도 분석에는 최소 2개 이상의 소자가 필요합니다')
            return

        # 주파수 입력 (기본값 13.56 MHz, Cal 결과와 무관)
        f_str, ok = QInputDialog.getText(
            self, '민감도 분석 — 주파수 설정',
            '분석 주파수 (MHz):',
            text='13.56'
        )
        if not ok or not f_str.strip():
            return
        try:
            f_mhz = float(f_str.strip())
        except ValueError:
            QMessageBox.warning(self, '입력 오류', '주파수를 숫자로 입력하세요')
            return
        if f_mhz <= 0:
            QMessageBox.warning(self, '입력 오류', '주파수는 0보다 커야 합니다')
            return

        self._run_sensitivity(f_mhz)

    def _run_sensitivity(self, f_mhz):
        """SensitivityWorker QThread 실행"""
        self.sens_btn.setEnabled(False)
        self.statusBar().showMessage('민감도 분석 중...')

        self._sens_thread = QThread()
        self._sens_worker = SensitivityWorker(
            components=self.circuit_canvas.components,
            wires=self.circuit_canvas.wires,
            f_hz=f_mhz * 1e6,
        )
        self._sens_worker.moveToThread(self._sens_thread)
        self._sens_thread.started.connect(self._sens_worker.run)
        self._sens_worker.finished.connect(
            lambda z_nom, res: self._on_sensitivity_finished(f_mhz, z_nom, res)
        )
        self._sens_worker.error.connect(self._on_sensitivity_error)
        self._sens_worker.finished.connect(self._sens_thread.quit)
        self._sens_worker.error.connect(self._sens_thread.quit)
        self._sens_thread.start()

    def _on_sensitivity_finished(self, f_mhz, z_nominal, results):
        self.sens_btn.setEnabled(True)
        self.statusBar().showMessage(
            f'민감도 분석 완료 @ {f_mhz:.3f} MHz  ({len(results)}개 소자)'
        )
        win = SensitivityWindow(f_mhz, z_nominal, results, self)
        win.show()

    def _on_sensitivity_error(self, msg):
        self.sens_btn.setEnabled(True)
        QMessageBox.warning(self, '민감도 오류', f'민감도 분석 오류:\n{msg}')
        self.statusBar().showMessage('민감도 분석 오류')

    def _on_calculate(self):
        """계산 버튼 처리"""
        # ── 에러 검증 ──
        # 1) 주파수 입력 확인
        try:
            f_start = float(self.freq_start.text())
            f_stop = float(self.freq_stop.text())
        except ValueError:
            QMessageBox.warning(self, '입력 오류', '주파수 값이 유효하지 않습니다')
            return

        if f_start <= 0 or f_stop <= 0:
            QMessageBox.warning(self, '입력 오류', '주파수는 0보다 커야 합니다')
            return

        # 2) 시작 >= 끝 검사
        if f_start >= f_stop:
            QMessageBox.warning(self, '입력 오류', '시작 주파수는 끝 주파수보다 작아야 합니다')
            return

        # 3~5) 회로 유효성 검증
        valid, err_msg = self.circuit_canvas.validate_circuit()
        if not valid:
            QMessageBox.warning(self, '회로 오류', err_msg)
            return

        # ── QThread 계산 시작 ──
        self.cal_btn.setEnabled(False)
        self.statusBar().showMessage('계산 중...')

        # 프로그레스 다이얼로그
        self.progress_dlg = QProgressDialog('임피던스 계산 중...', '취소', 0, 100, self)
        self.progress_dlg.setWindowModality(Qt.WindowModal)
        self.progress_dlg.setMinimumDuration(0)
        self.progress_dlg.setValue(0)

        self.calc_thread = QThread()
        self.calc_worker = MNAWorker(
            components=self.circuit_canvas.components,
            wires=self.circuit_canvas.wires,
            port_plus_pin=self.circuit_canvas.get_port_plus_pin(),
            port_gnd_pin=self.circuit_canvas.get_port_gnd_pin(),
            f_start=f_start * 1e6,
            f_stop=f_stop * 1e6
        )
        self.calc_worker.moveToThread(self.calc_thread)

        self.calc_thread.started.connect(self.calc_worker.run)
        self.calc_worker.progress.connect(self.progress_dlg.setValue)
        self.calc_worker.finished.connect(self._on_calc_finished)
        self.calc_worker.error.connect(self._on_calc_error)
        self.calc_worker.finished.connect(self.calc_thread.quit)
        self.calc_worker.error.connect(self.calc_thread.quit)

        self.progress_dlg.canceled.connect(self.calc_thread.quit)

        self.calc_thread.start()

    def _on_calc_finished(self, freqs_mhz, Z_array):
        """계산 완료 처리"""
        self.progress_dlg.close()
        self.cal_btn.setEnabled(True)
        self.last_freqs = freqs_mhz
        self.last_Z = Z_array

        # Smith Chart 업데이트
        self.smith_widget.update_data(freqs_mhz, Z_array)

        # Advanced 윈도우 업데이트 (열려있으면)
        if self.advanced_win and self.advanced_win.isVisible():
            self.advanced_win.update_data(freqs_mhz, Z_array)

        self.statusBar().showMessage(
            f'계산 완료 — {len(freqs_mhz):,}개 포인트 '
            f'({freqs_mhz[0]:.2f} ~ {freqs_mhz[-1]:.2f} MHz)'
        )

    def _on_calc_error(self, msg):
        """계산 오류 처리"""
        self.progress_dlg.close()
        self.cal_btn.setEnabled(True)
        QMessageBox.warning(self, '계산 오류', f'계산 중 오류 발생:\n{msg}')
        self.statusBar().showMessage('계산 오류')


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
