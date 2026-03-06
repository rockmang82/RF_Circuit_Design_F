# Circuit_SmithChart.py — 기능 추가 수정 명세서 v2 (최종)
# 대상: Claude Code Sonnet 4.6 | 기존 단일 .py 파일에 기능 추가 | 한국어 주석

---

## 작업 지시 원칙

- **전체 재작성 금지**: 아래 명시된 클래스·함수·UI 요소만 추가하거나 수정한다.
- **기존 코드 보존**: 수정하지 않는 모든 코드(한국어 주석 포함)는 원문 그대로 유지한다.
- **구현 자유도**: 명시되지 않은 세부 구현(내부 변수명, 헬퍼 함수, 레이아웃 미세 조정 등)은 개발자가 최적이라 판단하는 방식으로 자유롭게 결정한다.
- **참조 이미지**: 첨부된 참조 이미지(Sensitivity Analysis 결과 창 스크린샷)의 레이아웃과 시각적 스타일을 최대한 따른다.

---

## 수정 금지 항목 (명시적 보호)

아래 항목은 어떠한 경우에도 변경하지 않는다:

1. Smith Chart 계산 및 렌더링 로직 전체
2. MNA 솔버 내부 로직 (입력 인터페이스만 활용)
3. 기존 `Advanced` 버튼 및 임피던스 분석 창 (윈도우2)
4. 회로 편집 캔버스 (드래그, 배선, 삭제 등)
5. 모든 기존 한국어 주석
6. 기존 레이아웃 비율 (창1:창2 = 60:40)
7. 기존 Calculate 버튼 동작

---

## Feature: 회로 민감도 분석 (Local Sensitivity Analysis)

### 1. Component 클래스에 tolerance 속성 추가

기존 소자 데이터 구조(클래스 또는 dict)에 `tolerance` 필드를 추가한다.

| 항목 | 값 |
|------|-----|
| 속성명 | `tolerance` |
| 타입 | `float` (퍼센트 단위) |
| 기본값 | **5.0** (= ±5.0%) |
| 범위 | 0.0 ~ 100.0 |

### 2. 소자 값 입력 다이얼로그 수정

기존 소자 더블 클릭 시 열리는 값 입력 다이얼로그(`QInputDialog` 또는 커스텀 `QDialog`)에 **Tolerance 입력 필드**를 추가한다.

```
┌───────────────────────────────────┐
│  소자 값 입력                       │
├───────────────────────────────────┤
│                                   │
│  값:        [  50.0  ] Ω         │
│  Tolerance: [  5.0   ] %         │
│                                   │
│           [Cancel]  [OK]          │
└───────────────────────────────────┘
```

| 항목 | 상세 |
|------|------|
| 위젯 | `QDoubleSpinBox` |
| 범위 | 0.0 ~ 100.0 % |
| 소수점 | 1자리 |
| 기본값 | 5.0 |
| suffix | ` %` |

- 기존 `QInputDialog` 방식이면 커스텀 `QDialog`로 교체하여 값 + tolerance를 함께 입력받는다.
- 값 입력 필드의 기존 동작(단위 체계: R→Ω, L→nH, C→pF)은 그대로 유지한다.

---

### 3. "Sensitivity Analysis" 버튼 배치

**위치**: 창1(회로 설계 영역) 오른쪽 상단부

**스타일**: 기존 `"Advanced"` 버튼과 동일한 스타일 (배경색 `#4A90D9`, 흰색 텍스트, 동일 폰트/크기)

**클릭 시**: `SensitivityInputDialog` 인스턴스를 생성하여 `exec_()` 호출

---

### 4. 1단계 팝업 — 주파수 입력 (`SensitivityInputDialog`)

**클래스**: `SensitivityInputDialog(QDialog)` — 새로 작성

**모달리티**: Modal (`exec_()`)

```
┌─────────────────────────────────────────┐
│  Sensitivity Analysis — Frequency Input │
├─────────────────────────────────────────┤
│                                         │
│  Analysis Frequency:  [  13.56  ] MHz   │
│                                         │
│              [Cancel]  [Run Analysis]   │
└─────────────────────────────────────────┘
```

| 항목 | 상세 |
|------|------|
| 위젯 | `QDoubleSpinBox` |
| 범위 | 0.001 ~ 100000.0 MHz |
| 소수점 | 3자리 |
| 기본값 | 13.56 MHz |
| 단위 | MHz (위젯 오른쪽 레이블) |

동작:
- `Run Analysis` 클릭 또는 엔터 키 → `accept()`, 주파수값(Hz 변환) 반환
- `Cancel` 클릭 → `reject()`, 분석 중단

---

### 5. 민감도 계산 로직

**함수**: `compute_local_sensitivity(circuit, freq_hz)` — 새로 작성

#### 알고리즘 (중심 차분법)

```
입력:
  - circuit  : 현재 회로의 소자 리스트 (각 소자의 값, tolerance 포함)
  - freq_hz  : 분석 주파수 (Hz)

출력:
  - z_nominal : complex, 공칭 임피던스
  - results   : list of dict, 소자별 결과 (|S_normalized| 내림차순 정렬)
    {
      "name"         : str,   # 소자 식별자 (예: "R1", "C2", "L1")
      "value"        : float, # 공칭 소자값
      "unit"         : str,   # 단위 문자열 ("Ω", "nH", "pF")
      "tolerance"    : float, # ±% 값
      "delta_x"      : float, # 실제 사용된 perturbation 크기
      "S_normalized" : float, # 정규화 민감도 (무차원, 부호 포함)
      "S_absolute"   : float, # 절대 민감도 (Ω/단위, 부호 포함)
    }

절차:
  1. 공칭값으로 |Z_nominal| = |MNA 계산 결과| 산출
  2. 각 소자 Xi에 대해 (나머지 소자는 공칭값 고정):
     a. δ = Xi_nominal × (tolerance_i / 100)
     b. |Z_plus|  = |MNA( Xi = Xi_nominal + δ )|
     c. |Z_minus| = |MNA( Xi = Xi_nominal - δ )|
     d. dZ_dXi = (|Z_plus| - |Z_minus|) / (2 × δ)       ← 부호 보존
     e. S_normalized = dZ_dXi × (Xi_nominal / |Z_nominal|) ← 부호 보존
     f. S_absolute   = dZ_dXi                              ← 부호 보존
  3. results 리스트를 |S_normalized| 내림차순 정렬하여 반환
```

#### 예외 처리

| 상황 | 처리 |
|------|------|
| `Xi_nominal == 0` | `delta_x = 1e-9` 고정값 사용, 결과에 `"*"` 경고 표시 |
| MNA 계산 실패 | 해당 소자의 민감도를 `NaN`으로 기록, 나머지 계속 진행 |
| 소자 1개 이하 | 분석 생략, `QMessageBox.warning` 안내 |
| tolerance가 0% | `delta_x`를 공칭값의 1%로 대체 사용 |

---

### 6. 2단계 팝업 — 결과 창 (`SensitivityResultWindow`)

**클래스**: `SensitivityResultWindow(QDialog)` — 새로 작성

| 항목 | 값 |
|------|-----|
| 모달리티 | **Non-modal** (`show()` 호출) |
| 초기 크기 | 1000 × 500 px |
| 리사이즈 | 가능 |
| 중복 생성 | **여러 개 동시 오픈 허용** (서로 다른 주파수 결과 비교 가능) |
| 닫기 | 표준 X 버튼, 메인 윈도우에 영향 없음 |

**레이아웃**: `QSplitter(Qt.Horizontal)` — 좌우 50:50 비율

#### 6-1. 창 상단 정보 표시

```
분석 주파수: XX.XXX MHz   |   Z_nominal: XX.XX Ω   |   소자 수: N개
```

- 세 항목 모두 표시 (주파수, Z_nominal, 소자 수)
- 폰트: 볼드, 배경 구분 (참조 이미지 스타일 참조)

#### 6-2. 좌측 패널 — 수치 결과 테이블

`QTableWidget` 사용.

| # | 컬럼 헤더 | 데이터 | 정렬 | 비고 |
|---|-----------|--------|------|------|
| 1 | Component | 소자명 (R1, C2, L1 …) | 좌 | |
| 2 | Value | 공칭값 + 단위 (예: "100 pF") | 우 | |
| 3 | Tolerance | ±X.X% | 중앙 | |
| 4 | **ΔZ/ΔXi** | S_absolute (**부호 포함**, 소수 3자리) | 우 | 헤더에 절대값 기호 없음 |
| 5 | Norm. S | S_normalized (**부호 포함**, 소수 4자리) | 우 | |
| 6 | Rank | 민감도 순위 (1 = 가장 민감) | 중앙 | \|S_normalized\| 기준 |

**테이블 스타일**:
- 헤더: 굵게, 배경색 `#2d5986`, 흰색 텍스트
- 행 배경색: `|S_normalized|` 크기에 따라 그라데이션
  - 가장 민감 (Rank 1) → 연한 빨강 `#FFEBEE`
  - 가장 둔감 → 흰색 `#FFFFFF`
  - 보간: 순위에 따라 선형 보간
- 편집 불가: `setEditTriggers(QAbstractItemView.NoEditTriggers)`

#### 6-3. 우측 패널 — 토네이도 차트

`matplotlib` `FigureCanvasQTAgg` 임베드. **참조 이미지를 최대한 따른다.**

```
차트 구성:
- 종류: 수평 막대 그래프 (barh)
- Y축: 소자명, |S_normalized| 내림차순 (위 = 가장 민감)
- X축(하단, 주축): S_normalized 값 (부호 포함)
    양수 방향 → 임피던스 증가
    음수 방향 → 임피던스 감소
- X축 라벨: "Normalized Sensitivity (ΔZ/Z per ΔXi/Xi)"

막대 색상:
    S_normalized > 0  → '#d94f3d' (빨강 계열)
    S_normalized < 0  → '#4f7fd9' (파랑 계열)
    S_normalized == 0 → '#999999' (회색)

각 막대 끝에 수치 레이블: "S={값:.3f}" (오렌지색 텍스트)

차트 제목: "Tornado Chart — Local Sensitivity @ {freq:.3f} MHz"

두 번째 X축 (twin axis, 상단):
    S_absolute 값을 산점도(scatter)로 표시 (오렌지색 원형 마커)
    상단 X축 라벨: "Absolute Sensitivity |∂Z/∂Xi| (Ω/unit)"
    상단 X축 라벨 색상: 오렌지
```

---

### 7. 버튼 클릭 전체 흐름

```python
def on_sensitivity_analysis_clicked(self):
    # 0. 사전 검증: 소자가 2개 이상 연결되어 있는지 확인
    #    → 미달 시 QMessageBox.warning 후 return

    # 1단계: 주파수 입력 (Modal)
    dlg = SensitivityInputDialog(parent=self)
    if dlg.exec_() != QDialog.Accepted:
        return
    freq_hz = dlg.get_frequency_hz()

    # 민감도 계산
    try:
        z_nominal, results = compute_local_sensitivity(self.circuit, freq_hz)
    except Exception as e:
        QMessageBox.critical(self, "Analysis Error", str(e))
        return

    # 2단계: 결과 창 오픈 (Non-modal, 여러 개 동시 허용)
    result_win = SensitivityResultWindow(results, freq_hz, z_nominal, parent=self)
    result_win.show()

    # 참조 유지 — 리스트에 append하여 여러 창 동시 관리
    if not hasattr(self, '_sensitivity_windows'):
        self._sensitivity_windows = []
    # 닫힌 창 제거 후 추가
    self._sensitivity_windows = [w for w in self._sensitivity_windows if w.isVisible()]
    self._sensitivity_windows.append(result_win)
```

---

### 8. 구현 순서 권장

1. `Component` 클래스(또는 소자 데이터 구조)에 `tolerance` 속성 추가 (기본값 5.0)
2. 소자 더블 클릭 다이얼로그에 Tolerance `QDoubleSpinBox` 삽입
3. `compute_local_sensitivity()` 함수 구현 및 단독 테스트
4. `SensitivityInputDialog` 클래스 구현
5. `SensitivityResultWindow` 클래스 구현 (테이블 → 차트 순)
6. `"Sensitivity Analysis"` 버튼을 창1 오른쪽 상단부에 추가 및 클릭 핸들러 연결
7. 전체 통합 테스트: tolerance 0% 소자, 단일 소자 회로, 다중 결과 창 동시 오픈 확인

---

## 부록: 확정된 설계 결정 요약

| # | 항목 | 결정 |
|---|------|------|
| 1 | 결과 창 상단 정보 | 주파수 + Z_nominal + 소자 수 모두 표시 |
| 2 | ΔZ/ΔXi 컬럼 | 부호 포함 (헤더: `ΔZ/ΔXi`, 절대값 기호 없음) |
| 3 | 버튼 위치 | 창1 오른쪽 상단부 |
| 4 | Tolerance 기본값 | ±5.0% |
| 5 | 결과 창 중복 | 여러 개 동시 오픈 허용 (비교 가능) |
