import os
import re
import sys
import copy
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QFileDialog, QMessageBox, 
                             QComboBox, QLabel, QStackedWidget, QDialog, 
                             QColorDialog, QRadioButton, QScrollArea)
from PySide6.QtCore import Qt

class FileNameParser:
    @staticmethod
    def parse(filename):
        name_without_ext = os.path.splitext(filename)[0]
        readout_match = re.search(r'(\d+hr|\d+cyc)', name_without_ext, re.IGNORECASE)
        lot_match = re.search(r'(lot\d+)', name_without_ext, re.IGNORECASE)
        
        readout = readout_match.group(0) if readout_match else "Unknown_Readout"
        lot = lot_match.group(0) if lot_match else "Unknown_Lot"
        
        clean_name = name_without_ext
        if readout_match: clean_name = clean_name.replace(readout_match.group(0), "")
        if lot_match: clean_name = clean_name.replace(lot_match.group(0), "")
        clean_name = clean_name.replace("+", " ").strip()
        rel_name = clean_name.split()[0] if clean_name.split() else "Unknown_Rel"
        
        return rel_name, lot, readout

class RelDataProcessor:
    def __init__(self):
        self.raw_data = {}
        self.parsed_data = {}
        self.rel_name = ""
        self.lot_no = ""
        self.parameters = []
        self.history = []
        self.marker_colors = {}

    def load_file(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        if ext == '.csv':
            df = pd.read_csv(filepath, header=None, dtype=str)
        elif ext in ['.xlsx', '.xls']:
            df = pd.read_excel(filepath, header=None, dtype=str)
        else:
            raise ValueError("지원하지 않는 파일 형식입니다.")
        
        # applymap 대신 최신 pandas 권장 방식 사용 (속도 및 경고 방지)
        return df.map(lambda x: x.strip() if isinstance(x, str) else x)

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
            
            test_no_idx = df[df[0] == 'Test No.'].index
            if len(test_no_idx) == 0: continue
            start_row = test_no_idx[0] + 1
            
            sample_rows = [idx for idx in range(start_row, len(df)) if str(df.iloc[idx, 0]).isdigit()]
            
            if len(sample_rows) > 500:
                return f"시료수가 너무 많습니다. 500개 이하만 가능합니다. ({filename})"
            
            item_row_idx = df[df[5] == 'Item'].index
            unit_row_idx = df[df[5] == 'Unit'].index
            if len(item_row_idx) == 0 or len(unit_row_idx) == 0: continue
                
            item_row = item_row_idx[0]
            unit_row = unit_row_idx[0]
            bias_row = item_row + 4
            
            valid_cols = {}
            for c in range(6, df.shape[1]):
                p_name = df.iloc[item_row, c]
                p_unit = df.iloc[unit_row, c]
                p_bias = df.iloc[bias_row, c]
                
                if pd.isna(p_name) or p_name == "": continue
                if pd.isna(p_unit) or p_unit == "": continue
                
                header_name = f"{p_name}@{p_bias} ({p_unit})"
                valid_cols[c] = header_name

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
        self.history.append({
            'parsed_data': copy.deepcopy(self.parsed_data),
            'marker_colors': copy.deepcopy(self.marker_colors)
        })

    def undo(self):
        if not self.history: return False
        last_state = self.history.pop()
        self.parsed_data = last_state['parsed_data']
        self.marker_colors = last_state['marker_colors']
        return True

class DataEditDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("데이터 수정/삭제")
        self.initUI()
        
    def initUI(self):
        layout = QVBoxLayout(self)
        self.color_rb = QRadioButton("마커 색상 변경")
        self.color_rb.setChecked(True)
        self.delete_rb = QRadioButton("데이터 삭제")
        layout.addWidget(self.color_rb)
        layout.addWidget(self.delete_rb)
        
        self.del_group = QWidget()
        del_lay = QVBoxLayout(self.del_group)
        self.del_data_rb = QRadioButton("Data만 제거 (X축 유지)")
        self.del_row_rb = QRadioButton("시료(Unit) 행 전체 제거")
        self.del_data_rb.setChecked(True)
        del_lay.addWidget(self.del_data_rb)
        del_lay.addWidget(self.del_row_rb)
        layout.addWidget(self.del_group)
        self.del_group.setEnabled(False)
        
        self.color_rb.toggled.connect(lambda: self.del_group.setEnabled(not self.color_rb.isChecked()))
        
        btns = QHBoxLayout()
        ok = QPushButton("확인"); ok.clicked.connect(self.accept)
        can = QPushButton("취소"); can.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(can)
        layout.addLayout(btns)

    def get_action(self):
        if self.color_rb.isChecked(): return "COLOR", None
        return "DELETE", ("DATA_ONLY" if self.del_data_rb.isChecked() else "ROW_DELETE")

class MainApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reliability Data Analyzer")
        self.resize(1200, 800)
        self.processor = RelDataProcessor()
        self.initUI()
        
    def initUI(self):
        self.central_widget = QStackedWidget()
        self.setCentralWidget(self.central_widget)
        
        # 메뉴 1: 파일 업로드 화면
        self.page1 = QWidget()
        p1_lay = QVBoxLayout(self.page1)
        p1_lay.setAlignment(Qt.AlignCenter)
        
        lbl = QLabel("Reliability Analysis System")
        lbl.setStyleSheet("font-size: 22px; font-weight: bold; margin-bottom: 20px;")
        p1_lay.addWidget(lbl, alignment=Qt.AlignCenter)
        
        self.upload_btn = QPushButton("파일 업로드 (CSV / Excel 다중 선택)")
        self.upload_btn.setFixedSize(300, 50)
        self.upload_btn.clicked.connect(self.on_upload_clicked)
        p1_lay.addWidget(self.upload_btn, alignment=Qt.AlignCenter)
        self.central_widget.addWidget(self.page1)
        
        # 메뉴 2: 파라미터 선택 및 시각화 메인 화면
        self.page2 = QWidget()
        p2_lay = QVBoxLayout(self.page2)
        
        ctrl_bar = QHBoxLayout()
        ctrl_bar.addWidget(QLabel("분석 대상 Parameter:"))
        self.param_combo = QComboBox()
        self.param_combo.setMinimumWidth(250)
        ctrl_bar.addWidget(self.param_combo)
        
        btn_draw = QPushButton("그래프 출력")
        btn_draw.clicked.connect(self.draw_graphs)
        ctrl_bar.addWidget(btn_draw)
        
        btn_undo = QPushButton("되돌리기")
        btn_undo.clicked.connect(self.on_undo_clicked)
        ctrl_bar.addWidget(btn_undo)
        
        btn_pdf = QPushButton("PDF 레포트 저장")
        btn_pdf.clicked.connect(self.export_to_pdf)
        ctrl_bar.addWidget(btn_pdf)
        ctrl_bar.addStretch()
        p2_lay.addLayout(ctrl_bar)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        scroll.setWidget(self.scroll_content)
        p2_lay.addWidget(scroll)
        
        self.central_widget.addWidget(self.page2)

    def on_upload_clicked(self):
        files, _ = QFileDialog.getOpenFileNames(self, "파일 업로드", "", "Data Files (*.csv *.xlsx *.xls)")
        if not files: return
            
        # 1. 파일 분석 중 임시 상태 메시지 생성
        box = QMessageBox(QMessageBox.Information, "처리 중", "데이터를 분석 중입니다...", parent=self)
        box.setStandardButtons(QMessageBox.NoButton)
        box.show()
        QApplication.processEvents()
        
        result = self.processor.process_files(files)
        box.close()
        
        if result == "SUCCESS":
            # 2. 분석 완료 알림 노출 후 확인 버튼 이벤트 연동
            msg = QMessageBox(QMessageBox.Information, "완료", "분석하고자 하는 parameter를 선택하세요.", parent=self)
            msg.exec()
            
            self.param_combo.clear()
            self.param_combo.addItem("전체 선택 (ALL Parameters)")
            self.param_combo.addItems(self.processor.parameters)
            
            # 3. 확정 후 두 번째 메뉴 화면으로 상태 천이 보장
            self.central_widget.setCurrentIndex(1)
        else:
            QMessageBox.critical(self, "오류", result)

    def draw_graphs(self):
        while self.scroll_layout.count():
            child = self.scroll_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
                
        selected = self.param_combo.currentText()
        targets = self.processor.parameters if selected == "전체 선택 (ALL Parameters)" else [selected]
        
        readouts = list(self.processor.parsed_data.keys())
        cmap = plt.colormaps.get_cmap('tab20')
        self.ro_colors = {ro: cmap(i % 20) for i, ro in enumerate(readouts)}
        
        # Line Plots
        for param in targets:
            fig, ax = plt.subplots(figsize=(13, 1.57))
            fig.subplots_adjust(bottom=0.25, left=0.08, right=0.95, top=0.8)
            
            for readout, df in self.processor.parsed_data.items():
                if param in df.columns:
                    sub = df[param].dropna()
                    if sub.empty: continue
                    ax.plot(sub.index.tolist(), sub.values.tolist(), marker='o', linestyle='-', label=readout, color=self.ro_colors[readout], picker=True, pickradius=5)
                    
            ax.set_title(param, fontsize=10, fontweight='bold')
            ax.legend(loc='upper right', fontsize=8)
            ax.grid(True, linestyle='--', alpha=0.5)
            
            canvas = FigureCanvas(fig)
            canvas.mpl_connect('pick_event', lambda e, p=param: self.on_pick_element(e, p))
            self.scroll_layout.addWidget(canvas)
            
        # Box Plots
        grid_widget = QWidget()
        grid_layout = QHBoxLayout(grid_widget)
        grid_layout.setContentsMargins(0,0,0,0)
        col_idx = 0
        
        for param in targets:
            if col_idx % 4 == 0 and col_idx > 0:
                self.scroll_layout.addWidget(grid_widget)
                grid_widget = QWidget()
                grid_layout = QHBoxLayout(grid_widget)
                grid_layout.setContentsMargins(0,0,0,0)
                
            fig, ax = plt.subplots(figsize=(2.95, 3.54))
            fig.subplots_adjust(bottom=0.45, top=0.85, left=0.2, right=0.9)
            
            box_data, labels, colors, stats_text = [], [], [], ""
            for readout in readouts:
                df = self.processor.parsed_data.get(readout)
                if df is not None and param in df.columns:
                    sub = df[param].dropna().values
                    if len(sub) > 0:
                        box_data.append(sub)
                        labels.append(readout)
                        colors.append(self.ro_colors[readout])
                        stats_text += f"[{readout}]\nMin:{np.min(sub):.1f} Max:{np.max(sub):.1f}\nAVG:{np.mean(sub):.1f} s/s:{len(sub)} STD:{np.std(sub):.1f}\n"

            if box_data:
                bp = ax.boxplot(box_data, labels=labels, patch_artist=True)
                for patch, color in zip(bp['boxes'], colors):
                    patch.set_facecolor(color)
                    patch.set_alpha(0.6)
                for flier, color in zip(bp['fliers'], colors):
                    flier.set(marker='o', color=color, alpha=0.5)
                    
            ax.set_title(param, fontsize=8, fontweight='bold')
            ax.grid(True, linestyle=':', alpha=0.6)
            fig.text(0.1, 0.02, stats_text, fontsize=6, family='monospace', va='bottom')
            
            grid_layout.addWidget(FigureCanvas(fig))
            col_idx += 1
            
        if col_idx > 0:
            grid_layout.addStretch()
            self.scroll_layout.addWidget(grid_widget)
            
        QMessageBox.information(self, "완료", "data 분석이 완료 되었습니다.")

    def on_pick_element(self, event, param):
        line = event.artist
        readout = line.get_label()
        ind = event.ind[0]
        sub_df = self.processor.parsed_data[readout][param].dropna()
        unit_no = sub_df.index[ind]
        
        dialog = DataEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            action, del_type = dialog.get_action()
            self.processor.save_state()
            
            if action == "COLOR":
                color = QColorDialog.getColor()
                if color.isValid():
                    self.processor.marker_colors[(readout, unit_no, param)] = color.name()
            elif action == "DELETE":
                if del_type == "DATA_ONLY":
                    self.processor.parsed_data[readout].at[unit_no, param] = np.nan
                else:
                    self.processor.parsed_data[readout].drop(unit_no, inplace=True)
                self.draw_graphs()

    def on_undo_clicked(self):
        if self.processor.undo(): self.draw_graphs()

    def export_to_pdf(self):
        save_path, _ = QFileDialog.getSaveFileName(self, "PDF 저장", f"{self.processor.rel_name}_{self.processor.lot_no}.pdf", "PDF Files (*.pdf)")
        if not save_path: return
        from matplotlib.backends.backend_pdf import PdfPages
        with PdfPages(save_path) as pdf:
            fig = None
            for i, param in enumerate(self.processor.parameters):
                if i % 4 == 0:
                    if fig: pdf.savefig(fig); plt.close(fig)
                    fig = plt.figure(figsize=(11, 8.5))
                ax = fig.add_subplot(4, 1, (i % 4) + 1)
                for readout, df in self.processor.parsed_data.items():
                    if param in df.columns:
                        sub = df[param].dropna()
                        if not sub.empty: ax.plot(sub.index, sub.values, marker='o', color=self.ro_colors.get(readout))
                ax.set_title(param, fontsize=8)
                ax.grid(True, linestyle=':')
            if fig: pdf.savefig(fig); plt.close(fig)
        QMessageBox.information(self, "성공", "PDF 리포트 저장이 완료되었습니다.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainApp()
    window.show()
    sys.exit(app.exec())
