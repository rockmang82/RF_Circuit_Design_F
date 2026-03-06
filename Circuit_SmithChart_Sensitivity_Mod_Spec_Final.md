# Circuit_SmithChart.py — Sensitivity Analysis 수정 명세서 (Tolerance 제거 + 버튼 이동)
# 대상: Claude Code Sonnet 4.6 | 기존 Circuit_SmithChart.py 수정 | 한국어 주석

---

## 작업 지시 원칙

- **전체 재작성 금지**: 아래 명시된 부분만 수정한다.
- **기존 코드 보존**: 수정하지 않는 모든 코드(한국어 주석 포함)는 원문 그대로 유지한다.

### 수정 목적 (2가지)
1. **Tolerance 완전 제거**: 편미분 기반 민감도는 공칭값·토폴로지·주파수 세 가지만으로 계산된다. Tolerance는 불필요하므로 관련 코드를 모두 제거한다.
2. **Sensitivity Analysis 버튼 위치 변경**: 상단 툴바에서 **창1(회로 설계 영역) 오른쪽 상단부**로 이동하고, Advanced 버튼과 **동일한 높이(y 위치)**에 배치한다.

---

## 수정 금지 항목

1. Smith Chart 계산 및 렌더링 로직 전체
2. MNA 솔버 (`MNAWorker`, `mna_solve_one`) 내부 로직
3. 기존 `Advanced` 버튼 위치·스타일·동작
4. 회로 편집 캔버스 (`CircuitCanvas`) 내부 로직
5. 모든 기존 한국어 주석
6. 기존 레이아웃 비율 (창1:창2 = 60:40)
7. 기존 Calculate 버튼 동작
8. `Component` 클래스 (tolerance 속성 원래 없음)

---

## 수정 A: Sensitivity Analysis 버튼 위치 변경

### 현재 구조 (변경 전)

```
MainWindow
├── toolbar_widget (상단 툴바, QHBoxLayout)
│   ├── [R] [L] [C]
│   ├── 시작(MHz) / 끝(MHz) / [Cal]
│   ├── stretch
│   ├── [Sensitivity Analysis]  ← ★ 현재 위치: 상단 툴바 중앙
│   └── stretch
│
├── QSplitter (수평)
│   ├── CircuitCanvas (창1)  ← 직접 splitter에 추가됨
│   │
│   └── right_widget (창2 래퍼)
│       ├── adv_row (QHBoxLayout)
│       │   ├── stretch
│       │   └── [Advanced]  ← 우측 정렬, 높이 28px
│       └── SmithChartWidget
```

### 변경 후 구조

```
MainWindow
├── toolbar_widget (상단 툴바, QHBoxLayout)
│   ├── [R] [L] [C]
│   ├── 시작(MHz) / 끝(MHz) / [Cal]
│   └── stretch                    ← Sensitivity 버튼 제거됨
│
├── QSplitter (수평)
│   ├── left_widget (창1 래퍼) ★ 신규 래퍼 위젯
│   │   ├── sens_row (QHBoxLayout)
│   │   │   ├── stretch
│   │   │   └── [Sensitivity Analysis]  ← ★ 새 위치: 창1 우측 상단
│   │   └── CircuitCanvas (stretch=1)
│   │
│   └── right_widget (창2 래퍼)
│       ├── adv_row (QHBoxLayout)
│       │   ├── stretch
│       │   └── [Advanced]          ← 위치 변경 없음
│       └── SmithChartWidget
```

### 구체적 코드 변경

#### (1) 상단 툴바에서 Sensitivity 버튼 제거

```python
# ↓↓↓ 삭제할 블록 (라인 1489~1500 부근) ↓↓↓
toolbar_layout.addStretch()

# Sensitivity Analysis 버튼 (중앙)
self.sens_btn = QPushButton('Sensitivity Analysis')
self.sens_btn.setFixedHeight(28)
self.sens_btn.setStyleSheet(
    'background-color: #4A90D9; color: white; font-size: 10pt; border-radius: 3px;'
)
self.sens_btn.clicked.connect(self._open_sensitivity)
toolbar_layout.addWidget(self.sens_btn)

toolbar_layout.addStretch()
# ↑↑↑ 여기까지 삭제 ↑↑↑
```

Cal 버튼 뒤에 `addStretch()` 하나만 남긴다:
```python
# Cal 버튼 이후:
toolbar_layout.addStretch()
# (Sensitivity 버튼 코드 삭제됨)
main_layout.addWidget(toolbar_widget)
```

#### (2) 창1을 래퍼 위젯으로 감싸기

```python
# 변경 전:
# 창1: 회로 설계
self.circuit_canvas = CircuitCanvas()
splitter.addWidget(self.circuit_canvas)

# 변경 후:
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
```

#### 핵심 포인트
- `sens_row`의 구조(`addStretch` → 버튼)는 `adv_row`와 **동일한 패턴**이므로, 두 버튼이 각 패널의 우측 상단에 같은 높이로 정렬된다.
- `setFixedHeight(28)`로 Advanced 버튼(`setFixedSize(80, 28)`)과 높이를 일치시킨다.
- `left_layout.setContentsMargins(0, 0, 0, 0)`과 `right_layout.setContentsMargins(0, 0, 4, 0)`의 미세한 차이는 기존 코드를 그대로 존중한다.
- `left_layout.setSpacing(2)`로 `right_layout.setSpacing(2)`와 동일한 간격을 유지한다.

---

## 수정 B: Tolerance 관련 코드 완전 제거

### B-1. `_open_sensitivity()` 메서드 (MainWindow)

**삭제할 블록** — 공차 입력 QInputDialog 전체:
```python
# ↓↓↓ 삭제 ↓↓↓
# 공차 입력
tol_str, ok2 = QInputDialog.getText(
    self, '민감도 분석 — 공차 설정',
    '공차 (%, 예: 5.0):',
    text='5.0'
)
if not ok2:
    return
try:
    tolerance = float(tol_str.strip()) if tol_str.strip() else 5.0
except ValueError:
    tolerance = 5.0
# ↑↑↑ 삭제 ↑↑↑
```

**호출 변경**:
```python
# 변경 전:
self._run_sensitivity(f_mhz, tolerance)

# 변경 후:
self._run_sensitivity(f_mhz)
```

### B-2. `_run_sensitivity()` 메서드 (MainWindow)

```python
# 변경 전:
def _run_sensitivity(self, f_mhz, tolerance):
    ...
    self._sens_worker = SensitivityWorker(
        components=self.circuit_canvas.components,
        wires=self.circuit_canvas.wires,
        f_hz=f_mhz * 1e6,
        tolerance_pct=tolerance,
    )
    ...
    self._sens_worker.finished.connect(
        lambda res: self._on_sensitivity_finished(f_mhz, tolerance, res)
    )

# 변경 후:
def _run_sensitivity(self, f_mhz):
    ...
    self._sens_worker = SensitivityWorker(
        components=self.circuit_canvas.components,
        wires=self.circuit_canvas.wires,
        f_hz=f_mhz * 1e6,
    )
    ...
    self._sens_worker.finished.connect(
        lambda z_nom, res: self._on_sensitivity_finished(f_mhz, z_nom, res)
    )
```

### B-3. `_on_sensitivity_finished()` 메서드 (MainWindow)

```python
# 변경 전:
def _on_sensitivity_finished(self, f_mhz, tolerance, results):
    ...
    win = SensitivityWindow(f_mhz, tolerance, results, self)

# 변경 후:
def _on_sensitivity_finished(self, f_mhz, z_nominal, results):
    ...
    win = SensitivityWindow(f_mhz, z_nominal, results, self)
```

### B-4. `SensitivityWorker` 클래스

#### `__init__` 변경
```python
# 변경 전:
def __init__(self, components, wires, f_hz, tolerance_pct):
    super().__init__()
    import copy
    self.components = copy.deepcopy(components)
    self.wires = wires
    self.f_hz = f_hz
    self.eps = tolerance_pct / 100.0   # 상대 섭동량

# 변경 후:
def __init__(self, components, wires, f_hz):
    super().__init__()
    import copy
    self.components = copy.deepcopy(components)
    self.wires = wires
    self.f_hz = f_hz
    self.eps = 0.01   # 고정 상대 섭동량 (1%, 수치 미분 안정성 확보)
```

#### `finished` 시그널 변경
```python
# 변경 전:
finished = pyqtSignal(object)   # list of dict

# 변경 후:
finished = pyqtSignal(float, object)   # z_nominal_abs, list of dict
```

#### `run()` — emit 변경
```python
# 변경 전:
self.finished.emit(results)

# 변경 후:
self.finished.emit(abs(Z0), results)
```

> `run()` 내부의 중심 차분 계산 로직(`self.eps` 사용 부분)은 변경 없이 그대로 동작한다.

### B-5. `SensitivityWindow` 클래스

#### `__init__` 시그니처 변경
```python
# 변경 전:
def __init__(self, f_mhz, tolerance_pct, results, parent=None):
    ...
    self.f_mhz = f_mhz
    self.tolerance_pct = tolerance_pct
    self.results = results

# 변경 후:
def __init__(self, f_mhz, z_nominal, results, parent=None):
    ...
    self.f_mhz = f_mhz
    self.z_nominal = z_nominal   # |Z_nominal| in Ω
    self.results = results
```

#### `_build_ui()` — 헤더에 Z_nominal 추가
```python
# 변경 전:
header = QLabel(
    f'분석 주파수: {self.f_mhz:.3f} MHz   |   소자 수: {n}개'
)

# 변경 후:
header = QLabel(
    f'분석 주파수: {self.f_mhz:.3f} MHz   |   '
    f'Z_nominal: {self.z_nominal:.2f} Ω   |   소자 수: {n}개'
)
```

#### `_build_ui()` — 테이블: Tolerance 컬럼 제거, 6열→5열
```python
# 변경 전:
table = QTableWidget(n, 6)
table.setHorizontalHeaderLabels(
    ['Component', 'Value', 'Tolerance', '|ΔZ/ΔXi|', 'Norm. S', 'Rank']
)
...
tol_str = f'±{self.tolerance_pct:.1f}%'
sorted_r = sorted(self.results, key=lambda r: r['rank'])
for row_idx, r in enumerate(sorted_r):
    texts = [
        r['name'],
        r['val_str'],
        tol_str,
        f"{r['abs_sens']:.3f}",
        f"{r['norm_sens']:.4f}",
        str(r['rank']),
    ]

# 변경 후:
table = QTableWidget(n, 5)
table.setHorizontalHeaderLabels(
    ['Component', 'Value', 'ΔZ/ΔXi', 'Norm. S', 'Rank']
)
...
sorted_r = sorted(self.results, key=lambda r: r['rank'])
for row_idx, r in enumerate(sorted_r):
    texts = [
        r['name'],
        r['val_str'],
        f"{r['abs_sens']:.3f}",
        f"{r['norm_sens']:.4f}",
        str(r['rank']),
    ]
```

- `tol_str` 변수 선언 삭제
- texts 리스트에서 Tolerance 항목 제거
- 헤더: `|ΔZ/ΔXi|` → `ΔZ/ΔXi` (절대값 기호 제거, 부호 포함 값)

---

## 수정 체크리스트

| # | 위치 | 수정 내용 | 카테고리 |
|---|------|-----------|----------|
| 1 | `_build_ui()` 상단 툴바 | Sensitivity 버튼 코드 블록 삭제 (`addStretch` 2개 포함) | 버튼 이동 |
| 2 | `_build_ui()` 창1 영역 | `left_widget` 래퍼 생성, `sens_row`에 버튼 배치, `CircuitCanvas`를 래퍼 안에 추가 | 버튼 이동 |
| 3 | `_open_sensitivity()` | 공차 입력 QInputDialog 블록 삭제, `_run_sensitivity` 호출에서 tolerance 제거 | Tolerance 제거 |
| 4 | `_run_sensitivity()` | 시그니처에서 `tolerance` 제거, Worker 생성에서 `tolerance_pct` 제거, lambda 변경 | Tolerance 제거 |
| 5 | `_on_sensitivity_finished()` | `tolerance` → `z_nominal`, Window 생성 인자 변경 | Tolerance 제거 |
| 6 | `SensitivityWorker.__init__` | `tolerance_pct` 제거, `self.eps = 0.01` 고정 | Tolerance 제거 |
| 7 | `SensitivityWorker.finished` | 시그널 `pyqtSignal(float, object)` 변경 | Z_nominal 전달 |
| 8 | `SensitivityWorker.run()` | `emit(abs(Z0), results)` 변경 | Z_nominal 전달 |
| 9 | `SensitivityWindow.__init__` | `tolerance_pct` → `z_nominal` | Tolerance 제거 |
| 10 | `SensitivityWindow._build_ui()` 헤더 | Z_nominal 표시 추가 | Z_nominal 표시 |
| 11 | `SensitivityWindow._build_ui()` 테이블 | 6열→5열, Tolerance 컬럼 삭제, 헤더명 변경 | Tolerance 제거 |

---

## 변경하지 않는 부분 (명시적 확인)

- `SensitivityWorker.run()` 내부 중심 차분 계산 로직: `self.eps`를 사용하는 로직 자체는 그대로 유지
- 토네이도 차트 렌더링 로직: 변경 없음
- `mna_solve_one()` 함수: 변경 없음
- `Component` 클래스: 변경 없음
- `Advanced` 버튼 위치·스타일·동작: 변경 없음
- `right_widget` / `adv_row` 구조: 변경 없음

---

## 부록: 확정된 설계 결정 요약

| # | 항목 | 결정 |
|---|------|------|
| 1 | Sensitivity 버튼 위치 | 창1 오른쪽 상단부 (left_widget 래퍼 내 sens_row) |
| 2 | 버튼 높이 정렬 | Advanced 버튼과 동일 (setFixedHeight(28)) |
| 3 | 버튼 스타일 | Advanced 버튼과 동일 (#4A90D9, 흰색 텍스트, 10pt) |
| 4 | Perturbation 크기 | δ = Xi × 0.01 (1% 고정) |
| 5 | 테이블 컬럼 | 5열: Component, Value, ΔZ/ΔXi, Norm.S, Rank |
| 6 | 결과 창 상단 정보 | 주파수 + Z_nominal + 소자 수 |
| 7 | ΔZ/ΔXi 부호 | 부호 포함 (절대값 기호 없음) |
| 8 | 주파수 입력 | 기존 QInputDialog 유지 |
