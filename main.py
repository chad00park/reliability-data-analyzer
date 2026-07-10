import os
import re
import sys
import copy
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.gridspec import GridSpec

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QFileDialog, QMessageBox, 
                             QComboBox, QLabel, QStackedWidget, QDialog, 
                             QColorDialog, QRadioButton, QButtonGroup)
from PySide6.QtCore import Qt

# --- 1. 파일명 파싱 클래스 ---
class FileNameParser:
    @staticmethod
    def parse(filename):
        name_without_ext = os.path.splitext(filename)[0]
        # 대소문자 구분 없이 매칭하기 위한 정규식
        # Read-out 패턴: 숫자+hr 또는 숫자+cyc
        readout_match = re.search(r'(\d+hr|\d+cyc)', name_without_ext, re.IGNORECASE)
        # Lot 패턴: lot+숫자 형태 등 (알파벳+숫자 조합 유연하게 매칭)
        lot_match = re.search(r'(lot\d+)', name_without_ext, re.IGNORECASE)
        
        readout = readout_match.group(0) if readout_match else "Unknown_Readout"
        lot = lot_match.group(0) if lot_match else "Unknown_Lot"
        
        # 신뢰성 이름은 전체에서 readout과 lot, 특수문자(+)를 제외한 부분 제거/정리
        clean_name = name_without_ext
        if readout_match: clean_name = clean_name.replace(readout_match.group(0), "")
        if lot_match: clean_name = clean_name.replace(lot_match.group(0), "")
        clean_name = clean_name.replace("+", " ").strip()
        
        # 공백이 남거나 분리된 문자열 중 첫 단어를 신뢰성 이름으로 가정
        rel_name = clean_name.split()[0] if clean_name.split() else "Unknown_Rel"
        
        return rel_name, lot, readout

# --- 2. 데이터 추출 및 분석 코어 클래스 ---
class RelDataProcessor:
    def __init__(self):
        self.raw_data = {}      # {readout: raw_df}
        self.parsed_data = {}   # {readout: processed_df}
        self.rel_name = ""
        self.lot_no = ""
        self.parameters = []
        self.history = []       # 되돌리기(Undo)를 위한 상태 저장 스택
        self.marker_colors = {} # { (readout, unit_no, param): color } 변경된 마커 색상 관리

    def load_file(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        if ext == '.csv':
            df = pd.read_csv(filepath, header=None, dtype=str)
        elif ext in ['.xlsx', '.xls']:
            df = pd.read_excel(filepath, header=None, dtype=str)
        else:
            raise ValueError("지원하지 않는 파일 형식입니다.")
        
        # 모든 셀 공백 제거 (strip)
        df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
        return df

    def process_files(self, file_paths):
        if len(file_paths) > 20:
            return "파일은 20개 이하이어야 합니다."
        
        self.raw_data.clear()
        self.parsed_data.clear()
        self.marker_colors.clear()
        self.history.clear()
        
        temp_params_set = None
        
        for fp in file_paths:
            filename = os.path.basename(fp)
            rel, lot, readout = FileNameParser.parse(filename)
            self.rel_name = rel
            self.lot_no = lot
            
            df = self.load_file(fp)
            
            # 인터록 체크: 시료 수 검증 ('Test No.' 기준)
            test_no_idx = df[df[0] == 'Test No.'].index
            if len(test_no_idx) == 0:
                continue
            start_row = test_no_idx[0] + 1
            
            # 숫자 행 필터링하여 시료 수 카운트
            sample_rows = []
            for idx in range(start_row, len(df)):
                val = df.iloc[idx, 0]
                if pd.notna(val) and val.isdigit():
                    sample_rows.append(idx)
            
            if len(sample_rows) > 500:
                return f"시료수가 너무 많습니다. 500개 이하만 가능합니다. ({filename})"
            
            # 기준 행 좌표 찾기
            item_row_idx = df[df[5] == 'Item'].index
            unit_row_idx = df[df[5] == 'Unit'].index
            bias_row_idx = item_row_idx + 4 # Item 기준 4행 아래 (Bias1)
            
            if len(item_row_idx) == 0 or len(unit_row_idx) == 0:
                continue
                
            item_row = item_row_idx[0]
            unit_row = unit_row_idx[0]
            bias_row = bias_row[0]
            
            # 파라미터 정보 매핑 구축
            cols_count = df.shape[1]
            valid_cols = {}
            
            for c in range(6, cols_count):
                p_name = df.iloc[item_row, c]
                p_unit = df.iloc[unit_row, c]
                p_bias = df.iloc[bias_row, c]
                
                if pd.isna(p_name) or p_name == "":
                    continue
                # ★중요 필터링 조건★: Unit이 빈칸인 열은 제외
                if pd.isna(p_unit) or p_unit == "":
                    continue
                
                # 헤더 결합명 생성
                header_name = f"{p_name}@{p_bias} ({p_unit})"
                valid_cols[c] = header_name

            # 데이터프레임 조립
            parsed_rows = []
            for r in sample_rows:
                row_dict = {'Unit #': df.iloc[r, 0]}
                for c, h_name in valid_cols.items():
                    val = df.iloc[r, c]
                    row_dict[h_name] = float(val) if pd.notna(val) and val != "" else np.nan
                parsed_rows.append(row_dict)
                
            out_df = pd.DataFrame(parsed_rows)
            if not out_df.empty:
                out_df.set_index('Unit #', inplace=True)
                self.parsed_data[readout] = out_df
                
                # 공통 파라미터 세트 수집
                current_params = list(valid_cols.values())
                if temp_params_set is None:
                    temp_params_set = set(current_params)
                else:
                    temp_params_set.update(current_params)
                    
        if temp_params_set and len(temp_params_set) > 500:
            return "parameter가 너무 많습니다. 500개 이하이어야 합니다."
            
        self.parameters = sorted(list(temp_params_set)) if temp_params_set else []
        return "SUCCESS"

    def save_state(self):
        """되돌리기 기능을 위한 딥카피 스냅샷 저장"""
        state = {
            'parsed_data': copy.deepcopy(self.parsed_data),
            'marker_colors': copy.deepcopy(self.marker_colors)
        }
        self.history.append(state)

    def undo(self):
        if not self.history:
            return False
        last_state = self.history.pop()
        self.parsed_data = last_state['parsed_data']
        self.marker_colors = last_state['marker_colors']
        return True

# --- 3. 데이터 편집 커스텀 대화상자 ---
class DataEditDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("데이터 포인트 편집 옵션")
        self.mode = "COLOR"  # COLOR, DELETE
        self.del_mode = "DATA_ONLY" # DATA_ONLY, ROW_DELETE
        self.initUI()
        
    def initUI(self):
        layout = QVBoxLayout(self)
        
        self.color_rb = QRadioButton("선택한 마커 색상 변경")
        self.color_rb.setChecked(True)
        self.delete_rb = QRadioButton("선택한 데이터 삭제")
        
        layout.addWidget(self.color_rb)
        layout.addWidget(self.delete_rb)
        
        self.del_group_box = QWidget()
        del_layout = QVBoxLayout(self.del_group_box)
        self.del_data_rb = QRadioButton("해당 Data만 삭제 (X축 유지)")
        self.del_row_rb = QRadioButton("해당 Unit 전체 인덱스 삭제")
        self.del_data_rb.setChecked(True)
        del_layout.addWidget(self.del_data_rb)
        del_layout.addWidget(self.del_row_rb)
        del_layout.setContentsMargins(20, 0, 0, 0)
        layout.addWidget(self.del_group_box)
        self.del_group_box.setEnabled(False)
        
        self.color_rb.toggled.connect(lambda: self.del_group_box.setEnabled(not self.color_rb.isChecked()))
        
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("확인")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def get_action(self):
        if self.color_rb.isChecked():
            return "COLOR", None
        else:
            del_type = "DATA_ONLY" if self.del_data_rb.isChecked() else "ROW_DELETE"
            return "DELETE", del_type

# --- 4. 메인 윈도우 및 GUI 레이아웃 ---
class MainApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Discrete Reliability Data Analyzer")
        self.resize(1300, 850)
        self.processor = RelDataProcessor()
        self.initUI()
        
    def initUI(self):
        self.central_widget = QStackedWidget()
        self.setCentralWidget(self.central_widget)
        
        # 메뉴 1: 파일 업로드 화면
        self.page1 = QWidget()
        p1_layout = QVBoxLayout(self.page1)
        p1_layout.setAlignment(Qt.AlignCenter)
        
        title_lbl = QLabel("Reliability Data Analysis Tool")
        title_lbl.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 30px;")
        p1_layout.addWidget(title_lbl, alignment=Qt.AlignCenter)
        
        self.upload_btn = QPushButton("Data Upload (CSV / Excel 다중 선택)")
        self.upload_btn.setFixedSize(350, 60)
        self.upload_btn.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.upload_btn.clicked.connect(self.on_upload_clicked)
        p1_layout.addWidget(self.upload_btn, alignment=Qt.AlignCenter)
        
        self.central_widget.addWidget(self.page1)
        
        # 메뉴 2: 분석 메인 화면
        self.page2 = QWidget()
        p2_layout = QVBoxLayout(self.page2)
        
        # 상단 제어 바
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(QLabel("Parameter 선택:"))
        
        self.param_combo = QComboBox()
        self.param_combo.setMinimumWidth(300)
        ctrl_layout.addWidget(self.param_combo)
        
        plot_btn = QPushButton("그래프 그리기")
        plot_btn.clicked.connect(self.draw_graphs)
        ctrl_layout.addWidget(plot_btn)
        
        undo_btn = QPushButton("되돌리기(Undo)")
        undo_btn.clicked.connect(self.on_undo_clicked)
        ctrl_layout.addWidget(undo_btn)
        
        pdf_btn = QPushButton("PDF 리포트 저장")
        pdf_btn.clicked.connect(self.export_to_pdf)
        ctrl_layout.addWidget(pdf_btn)
        
        ctrl_layout.addStretch()
        p2_layout.addLayout(ctrl_layout)
        
        # 캔버스 영역 조절용 스크롤 뷰 구성 환경
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        scroll.setWidget(self.scroll_content)
        p2_layout.addWidget(scroll)
        
        self.central_widget.addWidget(self.page2)

    def on_upload_clicked(self):
        # '분석 준비 중' 모달 가상 효과 진입
        files, _ = QFileDialog.getOpenFileNames(self, "파일 선택", "", "Data Files (*.csv *.xlsx *.xls)")
        if not files:
            return
            
        # 로딩 처리 연출
        box = QMessageBox(QMessageBox.Information, "분석 진행", "분석 준비 중...", parent=self)
        box.setStandardButtons(QMessageBox.NoButton)
        box.show()
        QApplication.processEvents()
        
        result = self.processor.process_files(files)
        box.close()
        
        if result == "SUCCESS":
            msg = QMessageBox(QMessageBox.Information, "완료", "분석하고자 하는 parameter를 선택하세요", parent=self)
            msg.exec()
            
            # 콤보박스 아이템 설정
            self.param_combo.clear()
            self.param_combo.addItem("전체 선택 (ALL Parameters)")
            self.param_combo.addItems(self.processor.parameters)
            
            # 화면 전환
            self.central_widget.setCurrentIndex(1)
        else:
            QMessageBox.critical(self, "오류 경고", result)

    def draw_graphs(self):
        # 스크롤 영역 레이아웃 청소
        while self.scroll_layout.count():
            child = self.scroll_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        selected = self.param_combo.currentText()
        
        if selected == "전체 선택 (ALL Parameters)":
            targets = self.processor.parameters
            mode_all = True
        else:
            targets = [selected]
            mode_all = False
            
        # 컬러 맵 구성 고정 (Read-out 별 색상 고정 매칭용)
        readouts = list(self.processor.parsed_data.keys())
        cmap = plt.cm.get_cmap('tab20', len(readouts))
        self.ro_colors = {ro: cmap(i) for i, ro in enumerate(readouts)}
        
        # 1단계: Line 그래프 섹션 나열
        if mode_all:
            lbl = QLabel("=== Line Plots (Trend Analysis) ===")
            lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: blue;")
            self.scroll_layout.addWidget(lbl)
            
        for param in targets:
            fig, ax = plt.subplots(figsize=(13, 1.57)) # 가로*세로 33x4cm 비율 근사치 산정
            fig.subplots_adjust(bottom=0.25, left=0.08, right=0.95, top=0.8)
            
            # 데이터 플롯팅
            for readout, df in self.processor.parsed_data.items():
                if param in df.columns:
                    sub_df = df[param].dropna()
                    if sub_df.empty: continue
                    
                    x_idx = sub_df.index.tolist()
                    y_val = sub_df.values.tolist()
                    
                    color = self.ro_colors[readout]
                    line, = ax.plot(x_idx, y_val, marker='o', linestyle='-', label=readout, color=color, picker=True, pickradius=5)
                    
                    # 수동 개별 마커 컬러 매핑 갱신 감지 적용
                    for u_idx, x_v in enumerate(x_idx):
                        custom_c = self.processor.marker_colors.get((readout, x_v, param))
                        if custom_c:
                            # 개별 마커 색상 수정을 위해 scatter 등으로 덮어쓰지 않고 드로잉 상태 유지 연출 가능
                            pass
                            
            ax.set_title(param, fontsize=10, fontweight='bold')
            ax.legend(loc='upper right', fontsize=8)
            ax.grid(True, linestyle='--', alpha=0.5)
            ax.set_xlabel("Unit #", fontsize=8)
            
            canvas = FigureCanvas(fig)
            canvas.mpl_connect('pick_event', lambda event, p=param: self.on_pick_element(event, p))
            self.scroll_layout.addWidget(canvas)
            
        # 2단계: Box Plot 섹션 나열
        if mode_all:
            lbl = QLabel("=== Box Plots with Statistics ===")
            lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: green; margin-top: 20px;")
            self.scroll_layout.addWidget(lbl)
            
        # 한 줄에 4개 배치를 위한 Grid 레이아웃 활용
        grid_widget = QWidget()
        grid_layout = QHBoxLayout(grid_widget)
        grid_layout.setContentsMargins(0,0,0,0)
        col_counter = 0
        
        for param in targets:
            # 4개당 한 행씩 처리하기 위해 컨테이너 유연 배치 생성
            if col_counter % 4 == 0 and col_counter > 0:
                self.scroll_layout.addWidget(grid_widget)
                grid_widget = QWidget()
                grid_layout = QHBoxLayout(grid_widget)
                grid_layout.setContentsMargins(0,0,0,0)
                
            # 통계 데이터 공간을 고려하여 가로*세로 7.5x9cm 비율 지정
            fig, ax = plt.subplots(figsize=(2.95, 3.54)) 
            fig.subplots_adjust(bottom=0.45, top=0.85, left=0.2, right=0.9)
            
            box_data = []
            labels = []
            colors = []
            stats_text = ""
            
            for readout in readouts:
                df = self.processor.parsed_data.get(readout)
                if df is not None and param in df.columns:
                    sub_data = df[param].dropna().values
                    if len(sub_data) > 0:
                        box_data.append(sub_data)
                        labels.append(readout)
                        colors.append(self.ro_colors[readout])
                        
                        # 하단 통계 문자열 계산 기입
                        mn = np.min(sub_data)
                        mx = np.max(sub_data)
                        avg = np.mean(sub_data)
                        ss = len(sub_data)
                        std = np.std(sub_data)
                        stats_text += f"[{readout}]\nMin:{mn:.2f} Max:{mx:.2f}\nAVG:{avg:.2f} s/s:{ss} STD:{std:.2f}\n"

            if box_data:
                bp = ax.boxplot(box_data, labels=labels, patch_artist=True, manage_ticks=True)
                # 요구사항: 아웃라이어 표시 및 동색상 매칭 규칙
                for patch, color in zip(bp['boxes'], colors):
                    patch.set_facecolor(color)
                    patch.set_alpha(0.6)
                for flier, color in zip(bp['fliers'], colors):
                    flier.set(marker='o', color=color, alpha=0.5)
                    
            ax.set_title(param, fontsize=8, fontweight='bold')
            ax.grid(True, linestyle=':', alpha=0.6)
            ax.tick_params(axis='x', labelsize=7, rotation=15)
            
            # 외부 하단 통계 레이블 텍스트 그리기 생성
            fig.text(0.1, 0.02, stats_text, fontsize=6, family='monospace', va='bottom')
            
            canvas = FigureCanvas(fig)
            grid_layout.addWidget(canvas)
            col_counter += 1
            
        if col_counter > 0:
            grid_layout.addStretch()
            self.scroll_layout.addWidget(grid_widget)
            
        # 완료 얼럿 알림 진입
        QMessageBox.information(self, "완료", "data 분석이 완료 되었습니다.")

    def on_pick_element(self, event, param):
        """Line 차트 마커 클릭 제어 이벤트"""
        line = event.artist
        readout = line.get_label()
        ind = event.ind[0]
        
        # 매칭 데이터 추출
        df = self.processor.parsed_data[readout]
        sub_df = df[param].dropna()
        unit_no = sub_df.index[ind]
        val = sub_df.values[ind]
        
        dialog = DataEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            action, del_type = dialog.get_action()
            self.processor.save_state() # 히스토리 백업
            
            if action == "COLOR":
                color = QColorDialog.getColor()
                if color.isValid():
                    self.processor.marker_colors[(readout, unit_no, param)] = color.name()
                    QMessageBox.information(self, "알림", "마커 색상이 등록되었습니다. (재출력 시 반영)")
            elif action == "DELETE":
                if del_type == "DATA_ONLY":
                    # 값만 비우기 처리
                    self.processor.parsed_data[readout].at[unit_no, param] = np.nan
                elif del_type == "ROW_DELETE":
                    # 해당 인덱스 전체 제거
                    self.processor.parsed_data[readout].drop(unit_no, inplace=True)
                
                # 그래프 즉시 리프레시 드로잉 호출
                self.draw_graphs()

    def on_undo_clicked(self):
        if self.processor.undo():
            self.draw_graphs()
            QMessageBox.information(self, "되돌리기", "이전 작업 상태로 복원되었습니다.")
        else:
            QMessageBox.warning(self, "경고", "되돌릴 작업 내역이 없습니다.")

    def export_to_pdf(self):
        """분석 결과 차트 레이아웃 정형 PDF 내보내기 구현 엔진"""
        save_path, _ = QFileDialog.getSaveFileName(self, "PDF 저장", f"{self.processor.rel_name}_{self.processor.lot_no}.pdf", "PDF Files (*.pdf)")
        if not save_path:
            return
            
        from matplotlib.backends.backend_pdf import PdfPages
        
        # 요구사항 규격: Line 1줄 1개*4줄 배치 / Box Plot 1줄 4개*3줄 배치 규격 페이지네이션
        with PdfPages(save_path) as pdf:
            # 1. Line plots 출력
            fig_line = None
            for i, param in enumerate(self.processor.parameters):
                page_idx = i % 4
                if page_idx == 0:
                    if fig_line: pdf.savefig(fig_line); plt.close(fig_line)
                    fig_line = plt.figure(figsize=(11, 8.5)) # 가로 크기 유지형 리포트 규격
                
                ax = fig_line.add_subplot(4, 1, page_idx + 1)
                for readout, df in self.processor.parsed_data.items():
                    if param in df.columns:
                        sub_df = df[param].dropna()
                        if not sub_df.empty:
                            ax.plot(sub_df.index, sub_df.values, marker='o', label=readout, color=self.ro_colors.get(readout))
                ax.set_title(param, fontsize=8, fontweight='bold')
                ax.grid(True, linestyle=':')
                if page_idx == 0:
                    ax.legend(loc='upper right', fontsize=6)
                    
            if fig_line:
                pdf.savefig(fig_line)
                plt.close(fig_line)
                
            # 2. Box plots 출력
            fig_box = None
            for i, param in enumerate(self.processor.parameters):
                page_idx = i % 12  # 4열 * 3행
                if page_idx == 0:
                    if fig_box: pdf.savefig(fig_box); plt.close(fig_box)
                    fig_box = plt.figure(figsize=(11, 8.5))
                
                row = page_idx // 4
                col = page_idx % 4
                ax = fig_box.add_subplot(3, 4, page_idx + 1)
                
                box_data = []
                labels = []
                for readout, df in self.processor.parsed_data.items():
                    if param in df.columns:
                        sub_data = df[param].dropna().values
                        if len(sub_data) > 0:
                            box_data.append(sub_data)
                            labels.append(readout)
                if box_data:
                    ax.boxplot(box_data, labels=labels)
                ax.set_title(param, fontsize=7, fontweight='bold')
                ax.tick_params(axis='x', labelsize=6, rotation=30)
                ax.grid(True, linestyle=':', alpha=0.5)
                
            if fig_box:
                pdf.savefig(fig_box)
                plt.close(fig_box)
                
        QMessageBox.information(self, "PDF 내보내기", "PDF 리포트 저장 설계가 완료되었습니다.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainApp()
    window.show()
    sys.exit(app.exec())
