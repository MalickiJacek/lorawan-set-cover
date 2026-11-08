# -*- coding: utf-8 -*-
"""
LoRaWAN Set Cover Optimization v3.1 - Skrypt QGIS
===================================================
Skrypt do optymalizacji rozmieszczenia anten LoRaWAN metodą Set Cover.
Uruchamiany w konsoli Python QGIS.

v3.0 - Zmiany względem v2:
- Zastąpienie heurystyki zachłannej (greedy/SetCoverPy) dokładnym solverem ILP (PuLP/CBC)
- Tryb klasyczny: minimalizacja liczby anten przy pełnym pokryciu wszystkich budynków
  → gwarantowane globalne optimum (nie przybliżenie)
- Tryb z budżetem: maksymalizacja sumy lokali (LM+LU) przy zadanym limicie anten
  → przydatny gdy pokrycie 100% nie jest możliwe lub pożądane
- Konfigurowalny limit czasu solvera (domyślnie 300 s)
- Zachowanie wszystkich funkcji v2.0 (statystyki LM/LU, wizualizacja, scenariusze)

v3.1 - Zmiany względem v3.0:
- Automatyczny wybór solvera: CBC (wbudowany w PuLP) lub HiGHS (pip install highspy)
- Tworzenie zmiennych przez prob.add_variable (PuLP >= 3.3), z fallbackiem dla starszych wersji
- PuLP 4.0 zmienił API i nie zawiera CBC — wymagane pulp>=2.7,<4
- Jawny import NULL z qgis.core (skrypt działa także poza konsolą QGIS, np. jako Processing script)

Wymagane biblioteki (instalacja w OSGeo4W Shell):
pip install "pulp>=2.7,<4" pandas

Autor: Jacek Malicki | Licencja: MIT
"""

import os
import csv
import pandas as pd
import numpy as np

try:
    import pulp
except ImportError:
    raise ImportError(
        "Biblioteka 'pulp' nie jest zainstalowana.\n"
        "Uruchom w OSGeo4W Shell:\n"
        "pip install \"pulp>=2.7,<4\""
    )

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFillSymbol, QgsMarkerSymbol,
    QgsExpression, QgsProperty, NULL
)
from qgis.PyQt.QtWidgets import (
    QFileDialog, QMessageBox, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QGroupBox, QFormLayout,
    QCheckBox
)
from qgis.PyQt.QtGui import QColor
import processing


# =============================================================================
# KLASA DIALOGU KONFIGURACYJNEGO
# =============================================================================

class ConfigDialog(QDialog):
    """Dialog do konfiguracji parametrów analizy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Konfiguracja analizy LoRaWAN Set Cover v3.1")
        self.setMinimumWidth(640)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # --- Grupa: Pliki wejściowe ---
        files_group = QGroupBox("Pliki wejściowe")
        files_layout = QFormLayout()

        self.budynki_edit = QLineEdit()
        self.budynki_btn = QPushButton("Wybierz...")
        self.budynki_btn.clicked.connect(lambda: self.select_file(self.budynki_edit, "Budynki do pokrycia"))
        budynki_layout = QHBoxLayout()
        budynki_layout.addWidget(self.budynki_edit)
        budynki_layout.addWidget(self.budynki_btn)
        files_layout.addRow("Budynki do pokrycia (.shp):", budynki_layout)

        self.anteny_istn_edit = QLineEdit()
        self.anteny_istn_btn = QPushButton("Wybierz...")
        self.anteny_istn_btn.clicked.connect(lambda: self.select_file(self.anteny_istn_edit, "Anteny istniejące"))
        anteny_istn_layout = QHBoxLayout()
        anteny_istn_layout.addWidget(self.anteny_istn_edit)
        anteny_istn_layout.addWidget(self.anteny_istn_btn)
        files_layout.addRow("Anteny istniejące (.shp):", anteny_istn_layout)

        self.anteny_pot_edit = QLineEdit()
        self.anteny_pot_btn = QPushButton("Wybierz...")
        self.anteny_pot_btn.clicked.connect(lambda: self.select_file(self.anteny_pot_edit, "Anteny potencjalne"))
        anteny_pot_layout = QHBoxLayout()
        anteny_pot_layout.addWidget(self.anteny_pot_edit)
        anteny_pot_layout.addWidget(self.anteny_pot_btn)
        files_layout.addRow("Anteny potencjalne (.shp):", anteny_pot_layout)

        self.output_edit = QLineEdit()
        self.output_btn = QPushButton("Wybierz...")
        self.output_btn.clicked.connect(self.select_output_folder)
        output_layout = QHBoxLayout()
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(self.output_btn)
        files_layout.addRow("Folder wyjściowy:", output_layout)

        files_group.setLayout(files_layout)
        layout.addWidget(files_group)

        # --- Grupa: Parametry bufora ---
        buffer_group = QGroupBox("Parametry bufora")
        buffer_layout = QFormLayout()

        self.buffer_istn_mode = QComboBox()
        self.buffer_istn_mode.addItems(["Stała wartość", "Wzór z kolumną"])
        self.buffer_istn_mode.currentIndexChanged.connect(self.update_buffer_istn_ui)
        buffer_layout.addRow("Tryb bufora (istniejące):", self.buffer_istn_mode)

        self.buffer_istn_value = QLineEdit("500")
        buffer_layout.addRow("Wartość/Wzór (istniejące):", self.buffer_istn_value)

        self.buffer_pot_mode = QComboBox()
        self.buffer_pot_mode.addItems(["Stała wartość", "Wzór z kolumną"])
        self.buffer_pot_mode.currentIndexChanged.connect(self.update_buffer_pot_ui)
        buffer_layout.addRow("Tryb bufora (potencjalne):", self.buffer_pot_mode)

        self.buffer_pot_value = QLineEdit("500")
        buffer_layout.addRow("Wartość/Wzór (potencjalne):", self.buffer_pot_value)

        info_label = QLabel("Wzór: np. \"400+10*WYSOKOSC\" (nazwa kolumny w cudzysłowie)")
        info_label.setStyleSheet("color: gray; font-style: italic;")
        buffer_layout.addRow("", info_label)

        buffer_group.setLayout(buffer_layout)
        layout.addWidget(buffer_group)

        # --- Grupa: Nazwy kolumn ---
        columns_group = QGroupBox("Nazwy kolumn identyfikatorów")
        columns_layout = QFormLayout()

        self.id_column = QLineEdit("ID_SWDE")
        columns_layout.addRow("Kolumna ID (wszystkie pliki):", self.id_column)

        self.height_column = QLineEdit("WYSOKOSC")
        columns_layout.addRow("Kolumna wysokości (dla wzoru):", self.height_column)

        columns_group.setLayout(columns_layout)
        layout.addWidget(columns_group)

        # --- Grupa: Tryb wagowania i optymalizacji ---
        weights_group = QGroupBox("Tryb wagowania i optymalizacji")
        weights_layout = QFormLayout()

        self.use_weights_cb = QCheckBox("Włącz tryb wagowania (LM+LU)")
        weights_layout.addRow(self.use_weights_cb)

        self.lm_column = QLineEdit("LM")
        weights_layout.addRow("Kolumna LM (ilość lokali mieszkalnych):", self.lm_column)

        self.lu_column = QLineEdit("LU")
        weights_layout.addRow("Kolumna LU (ilość lokali użytkowych):", self.lu_column)

        self.min_apartments_threshold = QLineEdit("30")
        weights_layout.addRow("Próg 'mało istotnych' anten (min. lokali w zasięgu):", self.min_apartments_threshold)

        self.max_antennas_edit = QLineEdit("0")
        weights_layout.addRow("Limit anten — budżet (0 = minimalizuj liczbę):", self.max_antennas_edit)

        mode_info_label = QLabel(
            "0: ILP minimalizuje liczbę anten — pokrywa WSZYSTKIE budynki (klasyczny Set Cover)\n"
            ">0: ILP maksymalizuje pokrycie LM+LU w ramach podanego budżetu anten"
        )
        mode_info_label.setStyleSheet("color: gray; font-style: italic;")
        weights_layout.addRow("", mode_info_label)

        # Interakcja: włącz/wyłącz pola wagowania
        self.use_weights_cb.toggled.connect(self.lm_column.setEnabled)
        self.use_weights_cb.toggled.connect(self.lu_column.setEnabled)
        self.use_weights_cb.toggled.connect(self.min_apartments_threshold.setEnabled)
        self.use_weights_cb.toggled.connect(self.max_antennas_edit.setEnabled)
        self.lm_column.setEnabled(False)
        self.lu_column.setEnabled(False)
        self.min_apartments_threshold.setEnabled(False)
        self.max_antennas_edit.setEnabled(False)

        weights_group.setLayout(weights_layout)
        layout.addWidget(weights_group)

        # --- Grupa: Parametry solvera ILP ---
        solver_group = QGroupBox("Parametry solvera ILP")
        solver_layout = QFormLayout()

        self.solver_timeout_edit = QLineEdit("300")
        solver_layout.addRow("Limit czasu solvera (sekundy):", self.solver_timeout_edit)

        solver_info = QLabel(
            "Typowy czas: < 5 s dla < 500 anten | < 60 s dla < 2000 anten\n"
            "Przy przekroczeniu limitu — zwracane jest najlepsze znalezione rozwiązanie."
        )
        solver_info.setStyleSheet("color: gray; font-style: italic;")
        solver_layout.addRow("", solver_info)

        solver_group.setLayout(solver_layout)
        layout.addWidget(solver_group)

        # --- Przyciski ---
        buttons_layout = QHBoxLayout()
        self.ok_btn = QPushButton("Uruchom analizę")
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Anuluj")
        self.cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(self.ok_btn)
        buttons_layout.addWidget(self.cancel_btn)
        layout.addLayout(buttons_layout)

    def select_file(self, line_edit, title):
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Wybierz plik: {title}", "", "Shapefile (*.shp)"
        )
        if file_path:
            line_edit.setText(file_path)

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Wybierz folder wyjściowy")
        if folder:
            self.output_edit.setText(folder)

    def update_buffer_istn_ui(self, index):
        if index == 0:
            self.buffer_istn_value.setPlaceholderText("np. 500")
        else:
            self.buffer_istn_value.setPlaceholderText("np. 400+10*WYSOKOSC")

    def update_buffer_pot_ui(self, index):
        if index == 0:
            self.buffer_pot_value.setPlaceholderText("np. 500")
        else:
            self.buffer_pot_value.setPlaceholderText("np. 400+10*WYSOKOSC")

    def get_config(self):
        """Zwraca słownik z konfiguracją."""
        return {
            'budynki_path': self.budynki_edit.text(),
            'anteny_istn_path': self.anteny_istn_edit.text(),
            'anteny_pot_path': self.anteny_pot_edit.text(),
            'output_folder': self.output_edit.text(),
            'buffer_istn_mode': self.buffer_istn_mode.currentIndex(),
            'buffer_istn_value': self.buffer_istn_value.text(),
            'buffer_pot_mode': self.buffer_pot_mode.currentIndex(),
            'buffer_pot_value': self.buffer_pot_value.text(),
            'id_column': self.id_column.text(),
            'height_column': self.height_column.text(),
            'use_weights': self.use_weights_cb.isChecked(),
            'lm_column': self.lm_column.text(),
            'lu_column': self.lu_column.text(),
            'min_apartments_threshold': int(self.min_apartments_threshold.text() or '30'),
            'max_antennas': int(self.max_antennas_edit.text() or '0'),
            'solver_timeout': int(self.solver_timeout_edit.text() or '300'),
        }


# =============================================================================
# FUNKCJE POMOCNICZE
# =============================================================================

def get_solver(timeout):
    """
    Zwraca dostępny solver ILP.

    PuLP 2.x/3.x zawiera wbudowany solver CBC (PULP_CBC_CMD). Gdy go brak,
    używany jest HiGHS (pip install highspy).
    """
    available = pulp.listSolvers(onlyAvailable=True)
    if "PULP_CBC_CMD" in available:
        log_message(f"Solver: CBC (limit czasu: {timeout} s)")
        return pulp.PULP_CBC_CMD(msg=0, timeLimit=timeout)
    if "HiGHS" in available:
        log_message(f"Solver: HiGHS (limit czasu: {timeout} s)")
        return pulp.HiGHS(msg=False, timeLimit=timeout)
    if "HiGHS_CMD" in available:
        log_message(f"Solver: HiGHS_CMD (limit czasu: {timeout} s)")
        return pulp.HiGHS_CMD(msg=False, timeLimit=timeout)
    raise RuntimeError(
        "Brak dostępnego solvera ILP. Zainstaluj: pip install \"pulp>=2.7,<4\" (CBC) lub pip install highspy"
    )


def binary_var(prob, name):
    """Zmienna binarna: prob.add_variable (PuLP >= 3.3) lub LpVariable (starsze wersje)."""
    if hasattr(prob, "add_variable"):
        return prob.add_variable(name, cat="Binary")
    return pulp.LpVariable(name, cat="Binary")


def log_message(message):
    """Wyświetla komunikat w konsoli QGIS."""
    print(f"[LoRaWAN] {message}")


def load_layer(path, name):
    """Wczytuje warstwę wektorową."""
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid():
        raise Exception(f"Nie można wczytać warstwy: {path}")
    log_message(f"Wczytano warstwę: {name} ({layer.featureCount()} obiektów)")
    return layer


def create_centroids(layer, output_path, name):
    """Tworzy centroidy z warstwy poligonowej."""
    result = processing.run("native:centroids", {
        'INPUT': layer,
        'ALL_PARTS': False,
        'OUTPUT': output_path
    })
    centroid_layer = QgsVectorLayer(result['OUTPUT'], name, "ogr")
    log_message(f"Utworzono centroidy: {name}")
    return centroid_layer


def create_buffer(layer, distance_expr, output_path, name, is_expression=False):
    """
    Tworzy bufor wokół obiektów.

    Args:
        layer: warstwa wejściowa
        distance_expr: wartość odległości lub wyrażenie
        output_path: ścieżka do pliku wyjściowego
        name: nazwa warstwy
        is_expression: czy distance_expr to wyrażenie (True) czy stała wartość (False)
    """
    if is_expression:
        result = processing.run("native:buffer", {
            'INPUT': layer,
            'DISTANCE': QgsProperty.fromExpression(distance_expr),
            'SEGMENTS': 16,
            'END_CAP_STYLE': 0,
            'JOIN_STYLE': 0,
            'MITER_LIMIT': 2,
            'DISSOLVE': False,
            'OUTPUT': output_path
        })
    else:
        result = processing.run("native:buffer", {
            'INPUT': layer,
            'DISTANCE': float(distance_expr),
            'SEGMENTS': 16,
            'END_CAP_STYLE': 0,
            'JOIN_STYLE': 0,
            'MITER_LIMIT': 2,
            'DISSOLVE': False,
            'OUTPUT': output_path
        })

    buffer_layer = QgsVectorLayer(result['OUTPUT'], name, "ogr")
    log_message(f"Utworzono bufor: {name}")
    return buffer_layer


def remove_covered_buildings(buildings_layer, coverage_layer, output_path, name):
    """
    Usuwa budynki pokryte przez istniejące anteny (różnica przestrzenna).
    """
    result = processing.run("native:difference", {
        'INPUT': buildings_layer,
        'OVERLAY': coverage_layer,
        'OUTPUT': output_path
    })
    diff_layer = QgsVectorLayer(result['OUTPUT'], name, "ogr")
    log_message(f"Budynki po odjęciu zasięgu: {diff_layer.featureCount()} obiektów")
    return diff_layer


def spatial_join(buildings_layer, buffer_layer, output_path, id_column, prefix="A_"):
    """
    Wykonuje złączenie przestrzenne budynków z buforami anten.
    """
    result = processing.run("native:joinattributesbylocation", {
        'INPUT': buildings_layer,
        'JOIN': buffer_layer,
        'PREDICATE': [0],  # intersects
        'JOIN_FIELDS': [id_column],
        'METHOD': 0,  # one-to-many
        'DISCARD_NONMATCHING': True,
        'PREFIX': prefix,
        'OUTPUT': output_path
    })
    join_layer = QgsVectorLayer(result['OUTPUT'], "Budynki_z_antenami", "ogr")
    log_message(f"Złączenie przestrzenne: {join_layer.featureCount()} par budynek-antena")
    return join_layer


def export_to_csv(layer, output_path, building_id_col, antenna_id_col):
    """
    Eksportuje pary budynek-antena do pliku CSV.
    """
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Budynek_ID', 'Antena_ID'])

        for feature in layer.getFeatures():
            building_id = feature[building_id_col]
            antenna_id = feature[antenna_id_col]
            if building_id and antenna_id:
                writer.writerow([building_id, antenna_id])

    log_message(f"Wyeksportowano do CSV: {output_path}")


def compute_lm_lu_stats(layer, lm_column, lu_column):
    """
    Oblicza statystyki LM/LU dla warstwy.

    Returns:
        (count, sum_lm, sum_lu)
    """
    count = 0
    sum_lm = 0
    sum_lu = 0
    for feature in layer.getFeatures():
        count += 1
        lm_val = feature[lm_column]
        lu_val = feature[lu_column]
        sum_lm += int(lm_val) if lm_val and lm_val != NULL else 0
        sum_lu += int(lu_val) if lu_val and lu_val != NULL else 0
    return count, sum_lm, sum_lu


def build_weights_dict(layer, id_column, lm_column, lu_column):
    """
    Buduje słownik wag budynków: building_id -> LM+LU.

    Returns:
        dict: {building_id: waga (LM+LU, min 1)}
    """
    weights = {}
    for feature in layer.getFeatures():
        bid = str(feature[id_column])
        lm_val = feature[lm_column]
        lu_val = feature[lu_column]
        lm = int(lm_val) if lm_val and lm_val != NULL else 0
        lu = int(lu_val) if lu_val and lu_val != NULL else 0
        weights[bid] = max(lm + lu, 1)
    return weights


# =============================================================================
# FUNKCJE OPTYMALIZACJI ILP (zastępują greedy SetCoverPy z v2)
# =============================================================================

def run_set_cover_ilp(csv_path, timeout=300):
    """
    Klasyczny Set Cover przez ILP (PuLP: CBC lub HiGHS).

    Minimalizuje liczbę anten pokrywając WSZYSTKIE budynki z CSV.
    Zwraca dokładne globalne optimum (lub najlepsze znalezione przy timeout).

    Args:
        csv_path: ścieżka do CSV z parami budynek-antena
        timeout: limit czasu solvera w sekundach

    Returns:
        list: ID wybranych anten (jako string)
    """
    log_message("Uruchamianie ILP Set Cover (minimalizacja liczby anten)...")

    df = pd.read_csv(csv_path)

    if df.empty:
        log_message("UWAGA: Brak danych do analizy Set Cover!")
        return []

    antenna_ids = sorted(df["Antena_ID"].unique())
    building_ids = sorted(df["Budynek_ID"].unique())

    log_message(f"Liczba anten potencjalnych: {len(antenna_ids)}")
    log_message(f"Liczba budynków do pokrycia: {len(building_ids)}")

    # Mapowanie: budynek -> lista anten które go pokrywają
    antenna_to_idx = {aid: i for i, aid in enumerate(antenna_ids)}
    coverage = {bid: [] for bid in building_ids}
    for _, row in df.iterrows():
        coverage[row["Budynek_ID"]].append(row["Antena_ID"])

    # -------------------------------------------------------------------------
    # Model ILP: minimalizuj Σ x_i
    # s.t.  Σ_{i pokrywa j} x_i >= 1   dla każdego budynku j
    #        x_i ∈ {0, 1}
    # -------------------------------------------------------------------------
    prob = pulp.LpProblem("SetCover_Minimize", pulp.LpMinimize)

    x = [binary_var(prob, f"x_{i}") for i in range(len(antenna_ids))]

    prob += pulp.lpSum(x)

    for bid in building_ids:
        indices = [antenna_to_idx[aid] for aid in coverage[bid] if aid in antenna_to_idx]
        if indices:
            prob += pulp.lpSum(x[i] for i in indices) >= 1

    prob.solve(get_solver(timeout))

    status_name = pulp.LpStatus[prob.status]
    obj_val = pulp.value(prob.objective)
    log_message(f"Status solvera: {status_name} | Wybrano anten: {int(obj_val or 0)}")

    if prob.status == 1:
        log_message("Znaleziono dokładne globalne optimum.")
    elif prob.status == 0:
        log_message("UWAGA: Przekroczono limit czasu — używam najlepszego znalezionego rozwiązania.")
    else:
        log_message(f"BŁĄD: Solver zwrócił nieoczekiwany status '{status_name}'. Brak rozwiązania.")
        return []

    selected_antenna_ids = [
        str(antenna_ids[i]) for i in range(len(antenna_ids))
        if pulp.value(x[i]) is not None and pulp.value(x[i]) > 0.5
    ]

    log_message(f"Wybrano {len(selected_antenna_ids)} anten")

    # Weryfikacja pokrycia
    selected_set = set(selected_antenna_ids)
    covered = df[df["Antena_ID"].astype(str).isin(selected_set)]["Budynek_ID"].nunique()
    total = len(building_ids)
    pct = (covered / total * 100) if total > 0 else 0
    log_message(f"Pokrycie: {covered}/{total} budynków ({pct:.1f}%)")

    return selected_antenna_ids


def run_max_weighted_coverage_ilp(csv_path, weights, max_antennas, timeout=300):
    """
    Maksymalizacja ważonego pokrycia przy ograniczonym budżecie anten (ILP).

    Wybiera co najwyżej max_antennas anten tak, aby suma wag (LM+LU)
    pokrytych budynków była jak największa.

    Args:
        csv_path: ścieżka do CSV z parami budynek-antena
        weights: dict {building_id -> waga LM+LU} lub None (wagi = 1)
        max_antennas: maksymalna liczba anten do wybrania
        timeout: limit czasu solvera w sekundach

    Returns:
        list: ID wybranych anten (jako string)
    """
    log_message(f"Uruchamianie ILP Maksymalizacji Pokrycia (limit: {max_antennas} anten)...")

    df = pd.read_csv(csv_path)

    if df.empty:
        log_message("UWAGA: Brak danych do analizy!")
        return []

    antenna_ids = sorted(df["Antena_ID"].unique())
    building_ids = sorted(df["Budynek_ID"].unique())

    log_message(f"Liczba anten potencjalnych: {len(antenna_ids)}")
    log_message(f"Liczba budynków do pokrycia: {len(building_ids)}")

    # Wagi budynków
    w = {bid: (weights.get(str(bid), 1) if weights else 1) for bid in building_ids}
    total_possible_weight = sum(w.values())
    log_message(f"Łączna suma wag do pokrycia: {total_possible_weight}")

    # Mapowanie
    antenna_to_idx = {aid: i for i, aid in enumerate(antenna_ids)}
    coverage = {bid: [] for bid in building_ids}
    for _, row in df.iterrows():
        coverage[row["Budynek_ID"]].append(row["Antena_ID"])

    # -------------------------------------------------------------------------
    # Model ILP: maksymalizuj Σ w_j * y_j
    # s.t.  Σ x_i <= max_antennas
    #        y_j <= Σ_{i pokrywa j} x_i   dla każdego budynku j
    #        x_i, y_j ∈ {0, 1}
    # -------------------------------------------------------------------------
    prob = pulp.LpProblem("MaxWeightedCoverage", pulp.LpMaximize)

    x = [binary_var(prob, f"x_{i}") for i in range(len(antenna_ids))]
    y = [binary_var(prob, f"y_{j}") for j in range(len(building_ids))]

    prob += pulp.lpSum(w[building_ids[j]] * y[j] for j in range(len(building_ids)))

    prob += pulp.lpSum(x) <= max_antennas

    for j, bid in enumerate(building_ids):
        indices = [antenna_to_idx[aid] for aid in coverage[bid] if aid in antenna_to_idx]
        if indices:
            prob += y[j] <= pulp.lpSum(x[i] for i in indices)
        else:
            prob += y[j] == 0

    prob.solve(get_solver(timeout))

    status_name = pulp.LpStatus[prob.status]
    obj_val = pulp.value(prob.objective) or 0
    log_message(f"Status solvera: {status_name} | Suma pokrytych wag (LM+LU): {obj_val:.0f}")

    if prob.status == 1:
        log_message("Znaleziono dokładne globalne optimum.")
    elif prob.status == 0:
        log_message("UWAGA: Przekroczono limit czasu — używam najlepszego znalezionego rozwiązania.")
    else:
        log_message(f"BŁĄD: Solver zwrócił nieoczekiwany status '{status_name}'. Brak rozwiązania.")
        return []

    selected_antenna_ids = [
        str(antenna_ids[i]) for i in range(len(antenna_ids))
        if pulp.value(x[i]) is not None and pulp.value(x[i]) > 0.5
    ]
    covered_building_count = sum(
        1 for j in range(len(building_ids))
        if pulp.value(y[j]) is not None and pulp.value(y[j]) > 0.5
    )

    log_message(f"Wybrano {len(selected_antenna_ids)} anten (limit: {max_antennas})")
    log_message(f"Pokryto {covered_building_count}/{len(building_ids)} budynków")
    if total_possible_weight > 0:
        log_message(f"Pokryto {obj_val:.0f}/{total_possible_weight} lokali ({obj_val/total_possible_weight*100:.1f}%)")

    return selected_antenna_ids


# =============================================================================
# EKSPORT WYBRANYCH ANTEN DO CSV
# =============================================================================

def export_selected_antennas_csv(
        buffer_pot_path, buildings_path, id_column,
        selected_antennas, essential_antennas, redundant_antennas, unimportant_antennas,
        output_csv_path, use_weights=False, lm_column=None, lu_column=None):
    """
    Eksportuje szczegółowe informacje o wybranych antenach do pliku CSV.

    Kolumny:
        Antena_ID, Kategoria, Budynki, LM, LU,
        Pokrycie_unikalne_budynki, Pokrycie_unikalne_LM, Pokrycie_unikalne_LU,
        Nakladanie_pct

    Args:
        buffer_pot_path: ścieżka do pliku bufor_anteny_pot.shp
        buildings_path: ścieżka do budynki_niepokryte.shp
        id_column: nazwa kolumny ID
        selected_antennas: lista ID wybranych anten
        essential_antennas: lista ID anten niezbędnych
        redundant_antennas: lista ID anten nadmiarowych
        unimportant_antennas: lista ID anten mało istotnych
        output_csv_path: ścieżka do wyjściowego pliku CSV
        use_weights: czy obliczać LM/LU z atrybutów
        lm_column: nazwa kolumny LM (wymagana gdy use_weights=True)
        lu_column: nazwa kolumny LU (wymagana gdy use_weights=True)
    """
    log_message("Obliczanie szczegółowych statystyk wybranych anten...")

    buffer_layer = QgsVectorLayer(buffer_pot_path, "buffers", "ogr")
    buildings_layer = QgsVectorLayer(buildings_path, "buildings", "ogr")

    selected_set = set(selected_antennas)
    redundant_set = set(redundant_antennas)
    unimportant_set = set(unimportant_antennas)

    # Sprawdź czy kolumna Adres istnieje w warstwie buforów
    buffer_field_names = [field.name() for field in buffer_layer.fields()]
    has_adres = 'Adres' in buffer_field_names

    # Zbierz geometrie buforów wybranych anten (i opcjonalnie adresy)
    antenna_geoms = {}
    antenna_address = {}
    for f in buffer_layer.getFeatures():
        fid = str(f[id_column])
        if fid in selected_set:
            antenna_geoms[fid] = f.geometry()
            if has_adres:
                adres_val = f['Adres']
                antenna_address[fid] = str(adres_val) if adres_val and adres_val != NULL else ''

    # Zbierz dane budynków (id, geometria, LM, LU)
    building_data = []
    for b in buildings_layer.getFeatures():
        bid = str(b[id_column])
        lm = 0
        lu = 0
        if use_weights and lm_column and lu_column:
            lm_val = b[lm_column]
            lu_val = b[lu_column]
            lm = int(lm_val) if lm_val and lm_val != NULL else 0
            lu = int(lu_val) if lu_val and lu_val != NULL else 0
        building_data.append({'id': bid, 'geom': b.geometry(), 'lm': lm, 'lu': lu})

    # Dla każdej anteny: zbiór ID pokrytych budynków
    log_message(f"Sprawdzanie pokrycia dla {len(antenna_geoms)} anten ({len(building_data)} budynków)...")
    antenna_buildings = {}
    for idx, (aid, geom) in enumerate(antenna_geoms.items()):
        covered = set()
        for b in building_data:
            if geom.contains(b['geom']):
                covered.add(b['id'])
        antenna_buildings[aid] = covered
        if (idx + 1) % 10 == 0:
            log_message(f"  Przetworzono {idx + 1}/{len(antenna_geoms)} anten...")

    # Buduj wiersze CSV
    rows = []
    for aid in selected_antennas:
        geom = antenna_geoms.get(aid)
        covered_ids = antenna_buildings.get(aid, set())

        # Kategoria
        if aid in redundant_set:
            kategoria = "potencjalnie nadmiarowa"
        elif aid in unimportant_set:
            kategoria = "malo istotna"
        else:
            kategoria = "niezbedna"

        # Budynki i LM/LU w zasięgu
        budynki_count = len(covered_ids)
        lm_sum = sum(b['lm'] for b in building_data if b['id'] in covered_ids)
        lu_sum = sum(b['lu'] for b in building_data if b['id'] in covered_ids)

        # Unikalne pokrycie: budynki pokryte TYLKO przez tę antenę
        other_covered = set()
        for other_id, other_buildings in antenna_buildings.items():
            if other_id != aid:
                other_covered.update(other_buildings)
        unique_ids = covered_ids - other_covered
        unique_count = len(unique_ids)
        unique_lm = sum(b['lm'] for b in building_data if b['id'] in unique_ids)
        unique_lu = sum(b['lu'] for b in building_data if b['id'] in unique_ids)

        # Nakładanie bufora z innymi wybranymi antenami
        nakladanie_pct = 0.0
        if geom and geom.area() > 0:
            others = [g for oid, g in antenna_geoms.items() if oid != aid]
            if others:
                union_others = others[0]
                for g in others[1:]:
                    union_others = union_others.combine(g)
                intersection = geom.intersection(union_others)
                nakladanie_pct = (intersection.area() / geom.area()) * 100

        row = {
            'Antena_ID': aid,
            'Kategoria': kategoria,
            'Budynki': budynki_count,
            'LM': lm_sum,
            'LU': lu_sum,
            'Pokrycie_unikalne_budynki': unique_count,
            'Pokrycie_unikalne_LM': unique_lm,
            'Pokrycie_unikalne_LU': unique_lu,
            'Nakladanie_pct': round(nakladanie_pct, 1),
        }
        if has_adres:
            row['Adres'] = antenna_address.get(aid, '')
        rows.append(row)

    fieldnames = [
        'Antena_ID', 'Kategoria', 'Budynki', 'LM', 'LU',
        'Pokrycie_unikalne_budynki', 'Pokrycie_unikalne_LM', 'Pokrycie_unikalne_LU',
        'Nakladanie_pct'
    ]
    if has_adres:
        fieldnames.append('Adres')
    with open(output_csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)

    log_message(f"Wyeksportowano szczegóły {len(rows)} anten do: {output_csv_path}")
    return rows


# =============================================================================
# POZOSTAŁE FUNKCJE POMOCNICZE (bez zmian względem v2)
# =============================================================================

def style_layer_polygon(layer, color, opacity=0.5):
    """Stylizuje warstwę poligonową."""
    symbol = QgsFillSymbol.createSimple({
        'color': f'{color[0]},{color[1]},{color[2]},{int(opacity*255)}',
        'outline_color': f'{color[0]},{color[1]},{color[2]},255',
        'outline_width': '0.5'
    })
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()


def style_layer_point(layer, color, size=2):
    """Stylizuje warstwę punktową."""
    symbol = QgsMarkerSymbol.createSimple({
        'color': f'{color[0]},{color[1]},{color[2]},255',
        'size': str(size)
    })
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()


def filter_layer_by_ids(layer, id_column, ids_list):
    """
    Filtruje warstwę według listy ID.
    """
    if not ids_list:
        return

    ids_str = ",".join([f"'{id}'" for id in ids_list])
    filter_expr = f'"{id_column}" IN ({ids_str})'
    layer.setSubsetString(filter_expr)
    log_message(f"Zastosowano filtr: {len(ids_list)} obiektów")


def find_redundant_antennas(buffer_path, id_column, selected_ids, threshold=0.75):
    """
    Znajduje anteny, których bufor w >= threshold pokrywa się z buforami innych wybranych anten.
    Zwraca listę ID anten uznanych za nadmiarowe (możliwe do odrzucenia).
    """
    layer = QgsVectorLayer(buffer_path, "temp_buffer", "ogr")

    selected_geoms = {}
    for feature in layer.getFeatures():
        fid = str(feature[id_column])
        if fid in selected_ids:
            selected_geoms[fid] = feature.geometry()

    redundant_ids = []
    for aid, geom in selected_geoms.items():
        area = geom.area()
        if area == 0:
            continue

        others = [g for other_id, g in selected_geoms.items() if other_id != aid]
        if not others:
            continue

        union_geom = others[0]
        for g in others[1:]:
            union_geom = union_geom.combine(g)

        intersection = geom.intersection(union_geom)
        overlap_ratio = intersection.area() / area

        if overlap_ratio >= threshold:
            redundant_ids.append(aid)
            log_message(f"  Antena {aid}: pokrycie {overlap_ratio*100:.1f}% - możliwa do odrzucenia")

    return redundant_ids


def find_unimportant_antennas(buffer_path, buildings_path, id_column, selected_ids,
                              redundant_ids, max_count=5,
                              use_weights=False, weights_dict=None,
                              min_apartments_threshold=30):
    """
    Znajduje anteny o niskim znaczeniu.

    Jeśli use_weights=True: antena jest mało istotna gdy suma LM+LU budynków
    w jej zasięgu <= min_apartments_threshold.
    Jeśli use_weights=False: antena jest mało istotna gdy ma <= max_count budynków w zasięgu.
    """
    buffer_layer = QgsVectorLayer(buffer_path, "buffer_layer", "ogr")
    buildings_layer = QgsVectorLayer(buildings_path, "buildings", "ogr")

    antenna_geoms = {}
    for f in buffer_layer.getFeatures():
        fid = str(f[id_column])
        if fid in selected_ids:
            antenna_geoms[fid] = f.geometry()

    building_data = []
    for b in buildings_layer.getFeatures():
        bid = str(b[id_column])
        building_data.append((bid, b.geometry()))

    unimportant = []

    for aid, geom in antenna_geoms.items():
        if aid in redundant_ids:
            continue

        if use_weights and weights_dict:
            total_apartments = 0
            for bid, pt in building_data:
                if geom.contains(pt):
                    total_apartments += weights_dict.get(bid, 1)

            if total_apartments <= min_apartments_threshold:
                unimportant.append(aid)
                log_message(f"  Antena {aid}: {total_apartments} lokali w zasięgu — mało istotna (próg: {min_apartments_threshold})")
        else:
            count = 0
            for bid, pt in building_data:
                if geom.contains(pt):
                    count += 1

            if count <= max_count:
                unimportant.append(aid)
                log_message(f"  Antena {aid}: tylko {count} budynków w zasięgu — mało istotna")

    return unimportant


# =============================================================================
# GŁÓWNA FUNKCJA ANALIZY
# =============================================================================

def run_analysis(config):
    """
    Główna funkcja przeprowadzająca analizę Set Cover.
    """
    log_message("=" * 60)
    log_message("ROZPOCZĘCIE ANALIZY LORAWAN SET COVER v3.1")
    log_message("=" * 60)

    # Rozpakowanie konfiguracji
    budynki_path = config['budynki_path']
    anteny_istn_path = config['anteny_istn_path']
    anteny_pot_path = config['anteny_pot_path']
    output_folder = config['output_folder']
    buffer_istn_mode = config['buffer_istn_mode']
    buffer_istn_value = config['buffer_istn_value']
    buffer_pot_mode = config['buffer_pot_mode']
    buffer_pot_value = config['buffer_pot_value']
    id_column = config['id_column']
    height_column = config['height_column']
    use_weights = config['use_weights']
    lm_column = config['lm_column']
    lu_column = config['lu_column']
    min_apartments_threshold = config['min_apartments_threshold']
    max_antennas = config['max_antennas']
    solver_timeout = config['solver_timeout']

    # Walidacja ścieżek
    if not all([budynki_path, anteny_istn_path, anteny_pot_path, output_folder]):
        raise Exception("Nie wszystkie ścieżki zostały podane!")

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # =========================================================================
    # KROK 1: Wczytanie warstw wejściowych
    # =========================================================================
    log_message("\n--- KROK 1: Wczytanie warstw wejściowych ---")

    budynki_layer = load_layer(budynki_path, "Budynki_do_pokrycia")
    anteny_istn_layer = load_layer(anteny_istn_path, "Anteny_istniejace")
    anteny_pot_layer = load_layer(anteny_pot_path, "Anteny_potencjalne")

    crs = budynki_layer.crs()
    log_message(f"Układ współrzędnych: {crs.authid()}")

    # =========================================================================
    # KROK 2: Utworzenie centroidów
    # =========================================================================
    log_message("\n--- KROK 2: Tworzenie centroidów ---")

    centroidy_budynki = create_centroids(
        budynki_layer,
        os.path.join(output_folder, "centroidy_budynki.shp"),
        "Centroidy_budynkow"
    )

    centroidy_istn = create_centroids(
        anteny_istn_layer,
        os.path.join(output_folder, "centroidy_anteny_istn.shp"),
        "Centroidy_anten_istniejacych"
    )

    centroidy_pot = create_centroids(
        anteny_pot_layer,
        os.path.join(output_folder, "centroidy_anteny_pot.shp"),
        "Centroidy_anten_potencjalnych"
    )

    # =========================================================================
    # KROK 3: Bufor na istniejących antenach
    # =========================================================================
    log_message("\n--- KROK 3: Bufor istniejących anten ---")

    is_expr_istn = buffer_istn_mode == 1
    buffer_istn = create_buffer(
        centroidy_istn,
        buffer_istn_value,
        os.path.join(output_folder, "bufor_anteny_istn.shp"),
        "Bufor_anten_istniejacych",
        is_expression=is_expr_istn
    )

    # =========================================================================
    # KROK 4: Wyłączenie budynków pokrytych przez istniejące anteny
    # =========================================================================
    log_message("\n--- KROK 4: Wyłączenie budynków pokrytych ---")

    dissolved_istn = processing.run("native:dissolve", {
        'INPUT': buffer_istn,
        'OUTPUT': os.path.join(output_folder, "bufor_istn_dissolved.shp")
    })
    dissolved_istn_layer = QgsVectorLayer(
        dissolved_istn['OUTPUT'],
        "Bufor_istn_dissolved",
        "ogr"
    )

    budynki_niepokryte = processing.run("native:extractbylocation", {
        'INPUT': centroidy_budynki,
        'PREDICATE': [2],  # disjoint
        'INTERSECT': dissolved_istn_layer,
        'OUTPUT': os.path.join(output_folder, "budynki_niepokryte.shp")
    })
    budynki_niepokryte_layer = QgsVectorLayer(
        budynki_niepokryte['OUTPUT'],
        "Budynki_do_pokrycia_nowe",
        "ogr"
    )
    log_message(f"Budynki do pokrycia (po odjęciu): {budynki_niepokryte_layer.featureCount()}")

    # =========================================================================
    # KROK 5: Bufor na potencjalnych antenach
    # =========================================================================
    log_message("\n--- KROK 5: Bufor potencjalnych anten ---")

    is_expr_pot = buffer_pot_mode == 1
    buffer_pot = create_buffer(
        centroidy_pot,
        buffer_pot_value,
        os.path.join(output_folder, "bufor_anteny_pot.shp"),
        "Bufor_anten_potencjalnych",
        is_expression=is_expr_pot
    )

    # =========================================================================
    # KROK 5b: Budynki poza zasięgiem jakiejkolwiek potencjalnej anteny
    # =========================================================================
    log_message("\n--- KROK 5b: Budynki poza zasięgiem potencjalnych anten ---")

    dissolved_pot = processing.run("native:dissolve", {
        'INPUT': buffer_pot,
        'OUTPUT': os.path.join(output_folder, "bufor_pot_dissolved.shp")
    })
    dissolved_pot_layer = QgsVectorLayer(
        dissolved_pot['OUTPUT'],
        "Bufor_pot_dissolved",
        "ogr"
    )

    budynki_poza_zasiegiem = processing.run("native:extractbylocation", {
        'INPUT': budynki_niepokryte_layer,
        'PREDICATE': [2],  # disjoint
        'INTERSECT': dissolved_pot_layer,
        'OUTPUT': os.path.join(output_folder, "budynki_poza_zasiegiem.shp")
    })
    budynki_poza_zasiegiem_layer = QgsVectorLayer(
        budynki_poza_zasiegiem['OUTPUT'],
        "Budynki_poza_zasiegiem",
        "ogr"
    )
    count_poza = budynki_poza_zasiegiem_layer.featureCount()
    log_message(f"Budynki poza zasięgiem jakiejkolwiek potencjalnej anteny: {count_poza}")

    # =========================================================================
    # KROK 6: Złączenie przestrzenne budynków z buforami anten
    # =========================================================================
    log_message("\n--- KROK 6: Złączenie przestrzenne ---")

    join_path = os.path.join(output_folder, "budynki_anteny_join.shp")
    join_layer = spatial_join(
        budynki_niepokryte_layer,
        buffer_pot,
        join_path,
        id_column,
        prefix="A_"
    )

    # =========================================================================
    # KROK 7: Eksport do CSV
    # =========================================================================
    log_message("\n--- KROK 7: Eksport do CSV ---")

    csv_path = os.path.join(output_folder, "Macierz_GIS.csv")
    export_to_csv(join_layer, csv_path, id_column, f"A_{id_column}")

    # =========================================================================
    # KROK 7b: Budowa wag (jeśli tryb wagowania)
    # =========================================================================
    weights_dict = None
    if use_weights:
        log_message("\n--- KROK 7b: Budowa wag LM+LU ---")
        weights_dict = build_weights_dict(budynki_niepokryte_layer, id_column, lm_column, lu_column)
        total_weight = sum(weights_dict.values())
        log_message(f"Zbudowano wagi dla {len(weights_dict)} budynków (suma wag: {total_weight})")

    # =========================================================================
    # KROK 8: Algorytm Set Cover — ILP (PuLP/CBC)
    # =========================================================================
    log_message("\n--- KROK 8: Algorytm Set Cover ILP ---")

    if max_antennas > 0:
        if use_weights:
            log_message(f"Tryb: Maksymalizacja pokrycia LM+LU przy limicie {max_antennas} anten")
        else:
            log_message(f"Tryb: Maksymalizacja pokrycia budynków przy limicie {max_antennas} anten (wagi = 1)")
        selected_antennas = run_max_weighted_coverage_ilp(
            csv_path, weights_dict, max_antennas, timeout=solver_timeout
        )
    else:
        log_message("Tryb: Minimalizacja liczby anten (klasyczny Set Cover, dokładne optimum ILP)")
        selected_antennas = run_set_cover_ilp(csv_path, timeout=solver_timeout)

    # =========================================================================
    # KROK 9: Detekcja nadmiarowych anten
    # =========================================================================
    log_message("\n--- KROK 9: Detekcja nadmiarowych anten ---")

    buffer_pot_path = os.path.join(output_folder, "bufor_anteny_pot.shp")
    redundant_antennas = find_redundant_antennas(
        buffer_pot_path, id_column, selected_antennas, threshold=0.75
    )
    log_message(f"Anteny nadmiarowe (>=75% pokrycia z zasięgiem innych anten): {len(redundant_antennas)}")

    # =========================================================================
    # KROK 10: Detekcja anten o małej ilości budynków w zasięgu
    # =========================================================================
    log_message("\n--- KROK 10: Detekcja anten o niskim znaczeniu ---")

    buildings_path = os.path.join(output_folder, "centroidy_budynki.shp")
    buffer_pot_path = os.path.join(output_folder, "bufor_anteny_pot.shp")

    unimportant_antennas = find_unimportant_antennas(
        buffer_pot_path,
        buildings_path,
        id_column,
        selected_antennas,
        redundant_antennas,
        max_count=5,
        use_weights=use_weights,
        weights_dict=weights_dict,
        min_apartments_threshold=min_apartments_threshold
    )

    # Finalne anteny niezbędne
    essential_antennas = [
        a for a in selected_antennas
        if a not in redundant_antennas and a not in unimportant_antennas
    ]

    log_message(f"Anteny mało istotne: {len(unimportant_antennas)}")
    log_message(f"Anteny niezbędne po filtracji: {len(essential_antennas)}")

    # =========================================================================
    # KROK 10b: Eksport szczegółów wybranych anten do CSV
    # =========================================================================
    log_message("\n--- KROK 10b: Eksport wybranych anten do CSV ---")

    antennas_csv_path = os.path.join(output_folder, "wybrane_anteny.csv")
    export_selected_antennas_csv(
        buffer_pot_path=os.path.join(output_folder, "bufor_anteny_pot.shp"),
        buildings_path=os.path.join(output_folder, "budynki_niepokryte.shp"),
        id_column=id_column,
        selected_antennas=selected_antennas,
        essential_antennas=essential_antennas,
        redundant_antennas=redundant_antennas,
        unimportant_antennas=unimportant_antennas,
        output_csv_path=antennas_csv_path,
        use_weights=use_weights,
        lm_column=lm_column if use_weights else None,
        lu_column=lu_column if use_weights else None
    )

    # =========================================================================
    # KROK 11: Wizualizacja w QGIS
    # =========================================================================
    log_message("\n--- KROK 11: Wizualizacja ---")

    project = QgsProject.instance()

    project.addMapLayer(buffer_istn)
    style_layer_polygon(buffer_istn, (0, 0, 255), 0.3)

    if unimportant_antennas:
        buffer_unimportant = QgsVectorLayer(buffer_pot_path, "Zasieg_anten_maloznaczacych", "ogr")
        filter_layer_by_ids(buffer_unimportant, id_column, unimportant_antennas)
        project.addMapLayer(buffer_unimportant)
        style_layer_polygon(buffer_unimportant, (128, 0, 128), 0.5)

    if essential_antennas:
        buffer_essential = QgsVectorLayer(buffer_pot_path, "Zasieg_anten_niezbednych", "ogr")
        filter_layer_by_ids(buffer_essential, id_column, essential_antennas)
        project.addMapLayer(buffer_essential)
        style_layer_polygon(buffer_essential, (144, 238, 144), 0.5)

    if redundant_antennas:
        buffer_redundant = QgsVectorLayer(buffer_pot_path, "Zasieg_anten_nadmiarowych", "ogr")
        filter_layer_by_ids(buffer_redundant, id_column, redundant_antennas)
        project.addMapLayer(buffer_redundant)
        style_layer_polygon(buffer_redundant, (255, 165, 0), 0.5)

    if essential_antennas:
        centroidy_essential = QgsVectorLayer(
            os.path.join(output_folder, "centroidy_anteny_pot.shp"),
            "Anteny_niezbedne", "ogr"
        )
        filter_layer_by_ids(centroidy_essential, id_column, essential_antennas)
        project.addMapLayer(centroidy_essential)
        style_layer_point(centroidy_essential, (0, 128, 0), 2)

    if redundant_antennas:
        centroidy_redundant = QgsVectorLayer(
            os.path.join(output_folder, "centroidy_anteny_pot.shp"),
            "Anteny_nadmiarowe_do_odrzucenia", "ogr"
        )
        filter_layer_by_ids(centroidy_redundant, id_column, redundant_antennas)
        project.addMapLayer(centroidy_redundant)
        style_layer_point(centroidy_redundant, (255, 165, 0), 2)

    project.addMapLayer(centroidy_istn)
    style_layer_point(centroidy_istn, (0, 0, 255), 2)

    project.addMapLayer(centroidy_budynki)
    style_layer_point(centroidy_budynki, (255, 0, 0), 1)

    if budynki_poza_zasiegiem_layer.featureCount() > 0:
        project.addMapLayer(budynki_poza_zasiegiem_layer)
        style_layer_point(budynki_poza_zasiegiem_layer, (0, 0, 0), 2)

    # =========================================================================
    # KROK 12: Podsumowanie
    # =========================================================================
    log_message("\n--- KROK 12: Podsumowanie ---")

    all_buildings_count = centroidy_budynki.featureCount()
    already_covered_count = all_buildings_count - budynki_niepokryte_layer.featureCount()

    if use_weights:
        all_count, all_lm, all_lu = compute_lm_lu_stats(centroidy_budynki, lm_column, lu_column)
        total_count, total_lm, total_lu = compute_lm_lu_stats(budynki_niepokryte_layer, lm_column, lu_column)
        total_lokale = total_lm + total_lu
        already_cov_lm = all_lm - total_lm
        already_cov_lu = all_lu - total_lu

        uncov_count, uncov_lm, uncov_lu = compute_lm_lu_stats(budynki_poza_zasiegiem_layer, lm_column, lu_column)
        cov_count = total_count - uncov_count
        cov_lm = total_lm - uncov_lm
        cov_lu = total_lu - uncov_lu
        cov_lokale = cov_lm + cov_lu
        uncov_lokale = uncov_lm + uncov_lu

        pct_lokale = (cov_lokale / total_lokale * 100) if total_lokale > 0 else 0
        pct_uncov = (uncov_lokale / total_lokale * 100) if total_lokale > 0 else 0

        unimportant_label = f"<= {min_apartments_threshold} lokali w zasięgu"
    else:
        total_count = budynki_niepokryte_layer.featureCount()
        uncov_count = budynki_poza_zasiegiem_layer.featureCount()
        cov_count = total_count - uncov_count
        unimportant_label = "<= 5 budynków w zasięgu"

    log_message("\n" + "=" * 60)
    log_message("ANALIZA ZAKOŃCZONA POMYŚLNIE")
    log_message("=" * 60)

    log_message(f"\nWyniki:")
    if use_weights:
        log_message(f"Wszystkich budynków w analizie: {all_buildings_count} ({all_lm} LM, {all_lu} LU)")
        log_message(f"Budynków już pokrytych przez istniejące anteny: {already_covered_count} ({already_cov_lm} LM, {already_cov_lu} LU)")
    else:
        log_message(f"Wszystkich budynków w analizie: {all_buildings_count}")
        log_message(f"Budynków już pokrytych przez istniejące anteny: {already_covered_count}")

    if max_antennas > 0:
        log_message(f"\nWybrano {len(selected_antennas)} nowych anten (limit budżetu: {max_antennas}), w tym:")
    else:
        log_message(f"\nWybrano {len(selected_antennas)} nowych anten do maksymalnego pokrycia budynków, w tym:")
    log_message(f"  - {len(essential_antennas)} anten niezbędnych,")
    log_message(f"  - {len(redundant_antennas)} anten potencjalnie nadmiarowych (>=75% pokrycia z zasięgiem innych anten),")
    log_message(f"  - {len(unimportant_antennas)} anten o niskim znaczeniu ({unimportant_label}),")

    if use_weights:
        log_message(f"\nDo pokrycia (po odjęciu istniejących) było {total_count} budynków ({total_lm} LM, {total_lu} LU)")
        log_message(f"Maksymalnie pokrywalnych: {cov_count} budynków ({cov_lm} LM, {cov_lu} LU, {pct_lokale:.1f}% lokali)")
        log_message(f"Poza zasięgiem potencjalnych anten: {uncov_count} budynków ({uncov_lm} LM, {uncov_lu} LU, {pct_uncov:.1f}% lokali)")
    else:
        log_message(f"\nDo pokrycia (po odjęciu istniejących) było {total_count} budynków")
        pct = (cov_count / total_count * 100) if total_count > 0 else 0
        log_message(f"Maksymalnie pokrywalnych: {cov_count} budynków ({pct:.1f}%)")
        log_message(f"Poza zasięgiem potencjalnych anten: {uncov_count} budynków ({100-pct:.1f}%)")

    # =========================================================================
    # KROK 12b: Scenariusz bez anten o niskim znaczeniu
    # =========================================================================
    antennas_without_unimportant = [a for a in selected_antennas if a not in unimportant_antennas]
    num_without_unimp = len(antennas_without_unimportant)

    log_message(f"\n--- Scenariusz bez anten o niskim znaczeniu ---")
    log_message(f"Anteny w scenariuszu: {num_without_unimp} (= {len(selected_antennas)} wybranych - {len(unimportant_antennas)} mało istotnych)")

    if antennas_without_unimportant:
        buffer_without_unimp = QgsVectorLayer(buffer_pot_path, "Bufor_bez_maloznaczacych", "ogr")
        filter_layer_by_ids(buffer_without_unimp, id_column, antennas_without_unimportant)

        dissolved_without_unimp = processing.run("native:dissolve", {
            'INPUT': buffer_without_unimp,
            'OUTPUT': os.path.join(output_folder, "bufor_bez_maloznaczacych_dissolved.shp")
        })
        dissolved_without_unimp_layer = QgsVectorLayer(
            dissolved_without_unimp['OUTPUT'],
            "Bufor_bez_maloznaczacych_dissolved",
            "ogr"
        )

        budynki_poza_without_unimp = processing.run("native:extractbylocation", {
            'INPUT': budynki_niepokryte_layer,
            'PREDICATE': [2],  # disjoint
            'INTERSECT': dissolved_without_unimp_layer,
            'OUTPUT': os.path.join(output_folder, "budynki_poza_bez_maloznaczacych.shp")
        })
        budynki_poza_without_unimp_layer = QgsVectorLayer(
            budynki_poza_without_unimp['OUTPUT'],
            "Budynki_poza_bez_maloznaczacych",
            "ogr"
        )

        if use_weights:
            wu_uncov_count, wu_uncov_lm, wu_uncov_lu = compute_lm_lu_stats(
                budynki_poza_without_unimp_layer, lm_column, lu_column
            )
            wu_cov_count = total_count - wu_uncov_count
            wu_cov_lm = total_lm - wu_uncov_lm
            wu_cov_lu = total_lu - wu_uncov_lu
            wu_cov_lokale = wu_cov_lm + wu_cov_lu
            wu_uncov_lokale = wu_uncov_lm + wu_uncov_lu
            wu_pct = (wu_cov_lokale / total_lokale * 100) if total_lokale > 0 else 0
            wu_pct_uncov = (wu_uncov_lokale / total_lokale * 100) if total_lokale > 0 else 0

            log_message(f"{num_without_unimp} anten zdołało pokryć: {wu_cov_count} budynków ({wu_cov_lm} LM, {wu_cov_lu} LU, {wu_pct:.1f}% lokali)")
            log_message(f"{num_without_unimp} anten nie zdołało pokryć: {wu_uncov_count} budynków ({wu_uncov_lm} LM, {wu_uncov_lu} LU, {wu_pct_uncov:.1f}% lokali)")
        else:
            wu_uncov_count = budynki_poza_without_unimp_layer.featureCount()
            wu_cov_count = total_count - wu_uncov_count
            wu_pct = (wu_cov_count / total_count * 100) if total_count > 0 else 0

            log_message(f"{num_without_unimp} anten zdołało pokryć: {wu_cov_count} budynków ({wu_pct:.1f}%)")
            log_message(f"{num_without_unimp} anten nie zdołało pokryć: {wu_uncov_count} budynków ({100-wu_pct:.1f}%)")
    else:
        log_message("Brak anten po usunięciu mało istotnych.")

    log_message(f"\nWyniki zapisane w: {output_folder}")
    log_message(f"Szczegóły wybranych anten: {antennas_csv_path}")

    return selected_antennas


# =============================================================================
# PUNKT WEJŚCIA
# =============================================================================

def main():
    """Punkt wejścia skryptu."""
    dialog = ConfigDialog()

    if dialog.exec_() == QDialog.Accepted:
        config = dialog.get_config()

        try:
            selected = run_analysis(config)

            use_weights = config['use_weights']
            max_antennas = config['max_antennas']

            if max_antennas > 0 and use_weights:
                mode_info = f"ILP: maks. pokrycia LM+LU (limit {max_antennas} anten)"
            elif max_antennas > 0:
                mode_info = f"ILP: maks. pokrycia budynków (limit {max_antennas} anten)"
            else:
                mode_info = "ILP: minimalizacja liczby anten (dokładne optimum)"

            QMessageBox.information(
                None,
                "Sukces",
                f"Analiza zakończona pomyślnie!\n"
                f"Tryb: {mode_info}\n\n"
                f"Wybrano {len(selected)} anten.\n"
                f"Wyniki zapisane w:\n{config['output_folder']}\n\n"
                f"Szczegóły w konsoli Python."
            )
        except Exception as e:
            QMessageBox.critical(
                None,
                "Błąd",
                f"Wystąpił błąd podczas analizy:\n\n{str(e)}"
            )
            raise
    else:
        log_message("Anulowano przez użytkownika.")


# Uruchomienie skryptu
if __name__ == "__main__":
    main()
else:
    # Uruchomienie w konsoli QGIS
    main()
