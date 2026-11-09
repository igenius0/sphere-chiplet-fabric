"""Independent BER oracles and fail-closed physical-evidence ingestion tests.

Every input in this module is a test fixture, including dictionaries whose
declared origin is ``measured``. No physical device was measured for these tests.
"""
import copy
import csv
from decimal import Decimal, localcontext
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from sim.physical_validation import assess_measurements, binomial_upper_bound


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples' / 'measurements'


def declared_measured_fixture():
    """Small invented records exercising the declared-measured code path."""
    common = {'corner': 'nominal', 'origin': 'measured',
              'capture_id': 'TEST-FIXTURE-ONLY', 'instrument_id': 'fixture-instrument'}
    telemetry = []
    for quantity, channel, value in [('temperature_c', 'die0', 55),
                                     ('power_w', 'core', 20),
                                     ('voltage_v', 'core', 1),
                                     ('coolant_c', 'inlet', 20)]:
        telemetry.extend(dict(common, quantity=quantity, channel=channel,
                              timestamp_s=timestamp, value=value) for timestamp in (0, 1, 2))
    return {
        'schema_version': 1,
        'configuration': {
            'lanes': ['lane0'], 'corners': ['nominal'], 'rails': ['core'],
            'thermal_channels': ['die0'], 'coolant_channels': ['inlet'],
            'min_duration_s': 2, 'max_sample_gap_s': 1, 'confidence_level': .95,
            'limits': {'ber_upper_max': .001,
                       'temperature_c': {'die0': {'min': 0, 'max': 85}},
                       'power_w': {'core': {'min': 0, 'max': 30}},
                       'voltage_v': {'core': {'min': .9, 'max': 1.1}},
                       'coolant_c': {'inlet': {'min': 10, 'max': 40}}}},
        'identity': {'dut_id': 'TEST-FIXTURE-NO-DUT', 'dut_revision': 'test-only',
                     'board_id': 'TEST-FIXTURE-NO-BOARD', 'operator': 'unit test',
                     'laboratory': 'no physical laboratory'},
        'evidence': {'origin': 'measured', 'capture_id': 'TEST-FIXTURE-ONLY',
                     'captured_at': '2026-11-10T00:00:00Z',
                     'description': 'Invented unit-test input, never hardware evidence.'},
        'instruments': [{'instrument_id': 'fixture-instrument',
                         'serial_number': 'TEST-FIXTURE-NO-INSTRUMENT',
                         'calibration_id': 'TEST-FIXTURE-NO-CALIBRATION',
                         'calibrated_at': '2026-01-01T00:00:00Z',
                         'valid_until': '2027-01-01T00:00:00Z',
                         'uncertainty': {'ber': 0, 'temperature_c': .2, 'power_w': .05,
                                         'voltage_v': .002, 'coolant_c': .2},
                         'uncertainty_method': 'Invented test metadata.'}],
        'sources': {
            'electrical': {'origin': 'measured', 'capture_id': 'TEST-FIXTURE-ONLY',
                           'records': [dict(common, lane='lane0', start_s=0,
                                            end_s=2, errors=0, trial_bits=10000)]},
            'telemetry': {'origin': 'measured', 'capture_id': 'TEST-FIXTURE-ONLY',
                          'records': telemetry}}}


def issue_codes(report):
    return {issue['code'] for issue in report['issues']}


def independent_upper(errors, trials, confidence):
    """Invert the opposite binomial tail using direct, high-precision powers.

    This deliberately uses no production log-CDF, recurrence, or bound helper.
    It is practical for the small oracle cases, not production-size captures.
    """
    with localcontext() as context:
        context.prec = 120
        target = Decimal.from_float(confidence)
        low, high = Decimal(0), Decimal(1)
        for _ in range(400):
            p = (low + high) / 2
            probability = sum(Decimal(math.comb(trials, j)) * p ** j
                              * (1 - p) ** (trials - j)
                              for j in range(errors + 1, trials + 1))
            if probability < target:
                low = p
            else:
                high = p
        return float(high)


class BinomialBoundTests(unittest.TestCase):
    def test_hand_derived_cases_and_all_errors(self):
        for confidence in (.1, .5, .95, .999, 1e-20):
            self.assertAlmostEqual(binomial_upper_bound(0, 1, confidence), confidence,
                                   delta=max(1e-35, confidence * 2e-15))
            self.assertAlmostEqual(binomial_upper_bound(1, 2, confidence), math.sqrt(confidence),
                                   delta=math.sqrt(confidence) * 3e-15)
            self.assertEqual(binomial_upper_bound(10, 10, confidence), 1)
        self.assertAlmostEqual(binomial_upper_bound(1, 3, .5), .5, places=14)
        self.assertAlmostEqual(binomial_upper_bound(9, 10, .95), .95 ** .1, places=14)

    def test_nonzero_bounds_match_independent_high_precision_polynomial_oracle(self):
        for n, errors in ((3, 1), (5, 1), (5, 3), (10, 2), (10, 7), (25, 12), (40, 1)):
            for confidence in (.1, .5, .95, .999):
                with self.subTest(n=n, errors=errors, confidence=confidence):
                    expected = independent_upper(errors, n, confidence)
                    self.assertAlmostEqual(binomial_upper_bound(errors, n, confidence),
                                           expected, delta=expected * 2e-13)

    def test_zero_error_bound_preserves_precision_for_large_bit_count(self):
        n = 10 ** 18
        with localcontext() as context:
            context.prec = 100
            expected = float(1 - ((1 - Decimal.from_float(.95)).ln() / n).exp())
        actual = binomial_upper_bound(0, n)
        self.assertGreater(actual, 0)
        self.assertEqual(1 - math.exp(math.log(.05) / n), 0)
        self.assertAlmostEqual(actual, expected, delta=expected * 1e-15)

    def test_tiny_confidence_cannot_collapse_to_a_zero_upper_bound(self):
        self.assertEqual(binomial_upper_bound(0, 1, 1e-20), 1e-20)
        for errors, n in ((1, 100), (3, 5)):
            expected = independent_upper(errors, n, 1e-20)
            self.assertAlmostEqual(binomial_upper_bound(errors, n, 1e-20), expected,
                                   delta=expected * 3e-14)
        smallest = math.nextafter(0.0, 1.0)
        self.assertGreaterEqual(binomial_upper_bound(0, 10 ** 18, smallest), smallest)
        self.assertAlmostEqual(binomial_upper_bound(1, 2, smallest), math.sqrt(smallest),
                               delta=math.sqrt(smallest) * 2e-15)

    def test_large_trial_nonzero_reference_and_monotonicity(self):
        # Independently checked Beta(2, 10**18 - 1) 95% quantile.
        self.assertAlmostEqual(binomial_upper_bound(1, 10 ** 18), 4.743864518390577e-18,
                               delta=1e-31)
        values = [binomial_upper_bound(errors, 100) for errors in range(10)]
        self.assertTrue(all(a < b for a, b in zip(values, values[1:])))
        self.assertLess(binomial_upper_bound(1, 100, .9), binomial_upper_bound(1, 100, .99))
        self.assertGreater(binomial_upper_bound(0, 100), binomial_upper_bound(0, 1000))

    def test_near_one_confidence_cannot_lose_the_complementary_tail(self):
        for confidence in (1e-8, 1e-7, 1 - 1e-8, 1 - 1e-12, math.nextafter(1., 0.)):
            for errors in (1, 51, 60, 90):
                with self.subTest(confidence=confidence, errors=errors):
                    expected = independent_upper(errors, 100, confidence)
                    self.assertAlmostEqual(binomial_upper_bound(errors, 100, confidence),
                                           expected, delta=expected * 3e-14)

    def test_invalid_counts_confidence_and_unbounded_work_are_rejected(self):
        cases = [(True, 10, .95), (0, True, .95), (1.0, 10, .95),
                 (0, 10.0, .95), (-1, 10, .95), (11, 10, .95), (0, 0, .95),
                 (0, 10 ** 18 + 1, .95), (0, 10, 0), (0, 10, 1),
                 (0, 10, True), (0, 10, '0.95'), (0, 10, float('nan')),
                 (0, 10, float('inf')), (10001, 20004, .95)]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                binomial_upper_bound(*args)


class PhysicalEvidenceTests(unittest.TestCase):
    def assert_insufficient(self, manifest, code=None):
        report = assess_measurements(manifest)
        self.assertEqual(report['status'], 'INSUFFICIENT_EVIDENCE', report)
        if code:
            self.assertIn(code, issue_codes(report), report)
        json.dumps(report, allow_nan=False)
        return report

    def test_declared_measured_fixture_covers_every_profile_and_is_deterministic(self):
        manifest = declared_measured_fixture()
        original = copy.deepcopy(manifest)
        report = assess_measurements(manifest)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['numeric_status'], 'PASS')
        self.assertEqual(report['evidence_qualification'], 'QUALIFIED_MEASURED')
        self.assertEqual(report['issues'], [])
        self.assertEqual(len(report['electrical']), 1)
        self.assertEqual(len(report['telemetry']), 4)
        self.assertEqual(assess_measurements(manifest), report)
        self.assertEqual(manifest, original)
        json.dumps(report, allow_nan=False)

    def test_checked_in_examples_never_claim_physical_pass_even_after_manifest_relabel(self):
        missing = self.assert_insufficient(EXAMPLES / 'missing_hardware.json', 'MISSING_SOURCE')
        self.assertEqual(missing['numeric_status'], 'INSUFFICIENT_EVIDENCE')
        report = self.assert_insufficient(EXAMPLES / 'synthetic_manifest.json', 'NOT_MEASURED')
        self.assertEqual(report['numeric_status'], 'PASS')
        fixture = json.loads((EXAMPLES / 'synthetic_manifest.json').read_text())
        fixture['evidence']['origin'] = 'measured'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ('synthetic_electrical.json', 'synthetic_telemetry.csv'):
                (base / name).write_bytes((EXAMPLES / name).read_bytes())
            path = base / 'manifest.json'
            for relabel_sources in (False, True):
                if relabel_sources:
                    for source in fixture['sources'].values():
                        source['origin'] = 'measured'
                path.write_text(json.dumps(fixture))
                report = self.assert_insufficient(path, 'SOURCE_PROVENANCE')
                self.assertEqual(report['numeric_status'], 'PASS')
                self.assertIn('RECORD_PROVENANCE', issue_codes(report))

    def test_only_qualified_evidence_can_produce_overall_fail(self):
        for quantity in ('ber', 'temperature_c', 'power_w', 'voltage_v', 'coolant_c'):
            manifest = declared_measured_fixture()
            if quantity == 'ber':
                manifest['sources']['electrical']['records'][0]['errors'] = 100
            else:
                next(row for row in manifest['sources']['telemetry']['records']
                     if row['quantity'] == quantity)['value'] = 1e6
            with self.subTest(quantity=quantity):
                measured = assess_measurements(manifest)
                self.assertEqual(measured['status'], 'FAIL')
                self.assertEqual(measured['numeric_status'], 'FAIL')
                manifest['evidence']['origin'] = 'synthetic'
                unqualified = self.assert_insufficient(manifest, 'NOT_MEASURED')
                self.assertEqual(unqualified['numeric_status'], 'FAIL')

    def test_limits_are_inclusive_and_ber_uses_upper_bound_not_observed_zero(self):
        manifest = declared_measured_fixture()
        for row in manifest['sources']['telemetry']['records']:
            bounds = manifest['configuration']['limits'][row['quantity']][row['channel']]
            row['value'] = bounds['min'] if row['timestamp_s'] % 2 == 0 else bounds['max']
        upper = binomial_upper_bound(0, 10000)
        manifest['configuration']['limits']['ber_upper_max'] = upper
        self.assertEqual(assess_measurements(manifest)['status'], 'PASS')
        manifest['configuration']['limits']['ber_upper_max'] = upper / 2
        report = assess_measurements(manifest)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['electrical'][0]['observed_ber'], 0)
        self.assertIn('BER_LIMIT', issue_codes(report))

    def test_required_lane_corner_and_each_quantity_channel_are_not_optional(self):
        for name, value in (('lanes', 'lane1'), ('corners', 'hot')):
            manifest = declared_measured_fixture()
            manifest['configuration'][name].append(value)
            self.assert_insufficient(manifest, 'MISSING_LANE_CORNER')
        for quantity in ('temperature_c', 'power_w', 'voltage_v', 'coolant_c'):
            manifest = declared_measured_fixture()
            manifest['sources']['telemetry']['records'] = [
                row for row in manifest['sources']['telemetry']['records'] if row['quantity'] != quantity]
            with self.subTest(quantity=quantity):
                self.assert_insufficient(manifest, 'MISSING_CHANNEL_CORNER')
        for kind in ('electrical', 'telemetry'):
            manifest = declared_measured_fixture()
            manifest['sources'][kind]['records'] = []
            self.assert_insufficient(manifest, 'EMPTY_SOURCE')

    def test_electrical_observed_time_and_gap_coverage_use_positive_bit_intervals(self):
        for start, end, bits in ((.1, 2.1, 10000), (0, 1.9, 10000), (0, 2, 0)):
            manifest = declared_measured_fixture()
            row = manifest['sources']['electrical']['records'][0]
            row.update(start_s=start, end_s=end, trial_bits=bits)
            with self.subTest(start=start, end=end, bits=bits):
                self.assert_insufficient(manifest, 'ELECTRICAL_COVERAGE')
        manifest = declared_measured_fixture()
        row = manifest['sources']['electrical']['records'][0]
        manifest['sources']['electrical']['records'] = [dict(row, end_s=1), dict(row, start_s=2, end_s=3)]
        self.assertEqual(assess_measurements(manifest)['status'], 'PASS')
        manifest['sources']['electrical']['records'][1].update(start_s=2.01, end_s=3.01)
        self.assert_insufficient(manifest, 'ELECTRICAL_COVERAGE')
        # A zero-bit interval cannot fill an otherwise excessive active-data gap.
        manifest['sources']['electrical']['records'].insert(1, dict(row, start_s=1, end_s=2, trial_bits=0))
        self.assert_insufficient(manifest, 'ELECTRICAL_COVERAGE')

    def test_telemetry_start_end_sample_count_and_gaps_are_checked(self):
        mutations = (lambda rows: rows.pop(0), lambda rows: rows.pop(2),
                     lambda rows: rows.__setitem__(slice(0, 3), [rows[0]]),
                     lambda rows: rows[1].update(timestamp_s=1.1))
        for mutation in mutations:
            manifest = declared_measured_fixture()
            mutation(manifest['sources']['telemetry']['records'])
            self.assert_insufficient(manifest, 'TELEMETRY_COVERAGE')

    def test_overlapping_reordered_or_duplicate_records_are_invalid(self):
        for start, end in ((1, 3), (0, 2), (0, 1)):
            manifest = declared_measured_fixture()
            row = manifest['sources']['electrical']['records'][0]
            manifest['sources']['electrical']['records'].append(dict(row, start_s=start, end_s=end))
            self.assert_insufficient(manifest, 'INVALID_INPUT')
        for timestamp in (0, 2, -1):
            manifest = declared_measured_fixture()
            manifest['sources']['telemetry']['records'][1]['timestamp_s'] = timestamp
            self.assert_insufficient(manifest, 'INVALID_INPUT')

    def test_provenance_is_checked_at_manifest_source_record_and_capture_levels(self):
        for location in ('manifest', 'source', 'record'):
            for field, value in (('origin', 'synthetic'), ('origin', 'simulated'),
                                 ('origin', 'none'), ('capture_id', 'other-capture')):
                manifest = declared_measured_fixture()
                target = (manifest['evidence'] if location == 'manifest' else
                          manifest['sources']['electrical'] if location == 'source' else
                          manifest['sources']['electrical']['records'][0])
                target[field] = value
                with self.subTest(location=location, field=field, value=value):
                    self.assert_insufficient(manifest)

    def test_identity_and_evidence_fields_are_required_for_qualification(self):
        for section, field in (('identity', 'dut_id'), ('identity', 'dut_revision'),
                               ('identity', 'board_id'), ('identity', 'operator'),
                               ('identity', 'laboratory'), ('evidence', 'description'),
                               ('evidence', 'captured_at'), ('evidence', 'capture_id')):
            manifest = declared_measured_fixture()
            del manifest[section][field]
            self.assert_insufficient(manifest)
        for value in ('2026-11-10T00:00:00', 'yesterday', '', None):
            manifest = declared_measured_fixture()
            manifest['evidence']['captured_at'] = value
            self.assert_insufficient(manifest, 'EVIDENCE_INCOMPLETE')

    def test_instrument_identity_calibration_uncertainty_and_capture_end(self):
        mutations = [lambda m: m.update(instruments=[]),
                     lambda m: m['instruments'].append(copy.deepcopy(m['instruments'][0])),
                     lambda m: m['instruments'][0].pop('serial_number'),
                     lambda m: m['instruments'][0].pop('uncertainty_method'),
                     lambda m: m['instruments'][0]['uncertainty'].pop('ber'),
                     lambda m: m['instruments'][0]['uncertainty'].update(power_w=-.1),
                     lambda m: m['instruments'][0]['uncertainty'].update(power_w=True),
                     lambda m: m['instruments'][0]['uncertainty'].update(power_w=float('nan')),
                     lambda m: m['instruments'][0].update(calibrated_at='2026-11-10T00:00:01Z'),
                     lambda m: m['instruments'][0].update(valid_until='2026-11-10T00:00:01Z'),
                     lambda m: m['instruments'][0].update(valid_until='2026-11-10T00:00:02'),
                     lambda m: m['sources']['electrical']['records'][0].update(instrument_id='unknown')]
        for index, mutation in enumerate(mutations):
            manifest = declared_measured_fixture()
            mutation(manifest)
            with self.subTest(index=index):
                self.assert_insufficient(manifest)
        manifest = declared_measured_fixture()
        manifest['instruments'][0].update(calibrated_at='2026-11-10T02:00:00+02:00',
                                        valid_until='2026-11-10T02:00:02+02:00')
        self.assertEqual(assess_measurements(manifest)['status'], 'PASS')

    def test_strict_schema_numeric_ranges_boolean_nan_counts_and_unknown_names(self):
        cases = [(('schema_version',), True), (('schema_version',), 1.0),
                 (('extra',), 1), (('configuration', 'confidence_level'), 0),
                 (('configuration', 'confidence_level'), 1),
                 (('configuration', 'confidence_level'), True),
                 (('configuration', 'min_duration_s'), 0),
                 (('configuration', 'max_sample_gap_s'), float('inf')),
                 (('configuration', 'lanes'), ['lane0', 'lane0']),
                 (('configuration', 'rails'), []),
                 (('configuration', 'limits', 'temperature_c', 'die0', 'min'), 90),
                 (('configuration', 'limits', 'ber_upper_max'), -1),
                 (('configuration', 'limits', 'ber_upper_max'), 1.1),
                 (('sources', 'electrical', 'records', 0, 'errors'), True),
                 (('sources', 'electrical', 'records', 0, 'errors'), 1.0),
                 (('sources', 'electrical', 'records', 0, 'errors'), -1),
                 (('sources', 'electrical', 'records', 0, 'errors'), 10001),
                 (('sources', 'electrical', 'records', 0, 'trial_bits'), 10 ** 18 + 1),
                 (('sources', 'electrical', 'records', 0, 'start_s'), True),
                 (('sources', 'electrical', 'records', 0, 'end_s'), 0),
                 (('sources', 'electrical', 'records', 0, 'lane'), 'unconfigured'),
                 (('sources', 'electrical', 'records', 0, 'corner'), 'hot'),
                 (('sources', 'electrical', 'records', 0, 'extra'), 0),
                 (('sources', 'telemetry', 'records', 0, 'value'), float('nan')),
                 (('sources', 'telemetry', 'records', 0, 'value'), float('inf')),
                 (('sources', 'telemetry', 'records', 0, 'value'), True),
                 (('sources', 'telemetry', 'records', 0, 'timestamp_s'), -1),
                 (('sources', 'telemetry', 'records', 0, 'quantity'), 'energy_j'),
                 (('sources', 'telemetry', 'records', 0, 'channel'), 'unknown')]
        for path, value in cases:
            manifest = declared_measured_fixture()
            target = manifest
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path, value=value):
                self.assert_insufficient(manifest, 'INVALID_INPUT')
        for manifest in (None, [], '', 1, False, {1: 2}):
            self.assert_insufficient(manifest)

    def test_aggregate_counter_and_arithmetic_limits_fail_closed(self):
        manifest = declared_measured_fixture()
        row = manifest['sources']['electrical']['records'][0]
        manifest['sources']['electrical']['records'] = [dict(row, trial_bits=10 ** 18),
                                                       dict(row, start_s=2, end_s=4, trial_bits=1)]
        self.assert_insufficient(manifest, 'INVALID_INPUT')
        manifest = declared_measured_fixture()
        manifest['sources']['electrical']['records'][0].update(errors=10001, trial_bits=20004)
        self.assert_insufficient(manifest, 'INVALID_INPUT')
        with mock.patch('sim.physical_validation.MAX_TOTAL_BINOMIAL_TERMS', 0):
            self.assert_insufficient(declared_measured_fixture(), 'INVALID_INPUT')
        with mock.patch('sim.physical_validation.MAX_PROFILES', 1):
            self.assert_insufficient(declared_measured_fixture(), 'INVALID_INPUT')


class PhysicalFileAndCliTests(unittest.TestCase):
    assert_insufficient = PhysicalEvidenceTests.assert_insufficient

    def file_manifest(self, base, kind='electrical', source_format='json'):
        manifest = declared_measured_fixture()
        source = manifest['sources'][kind]
        rows = source.pop('records')
        source.update(path=f'{kind}.{source_format}', format=source_format)
        if source_format == 'json':
            content = json.dumps({'schema_version': 1, 'origin': source['origin'],
                                  'capture_id': source['capture_id'], 'records': rows})
        else:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            content = output.getvalue()
        (base / source['path']).write_text(content)
        path = base / 'manifest.json'
        path.write_text(json.dumps(manifest))
        return manifest, path, base / source['path']

    def test_file_formats_relative_resolution_and_content_hash(self):
        for kind in ('electrical', 'telemetry'):
            for source_format in ('csv', 'json'):
                with self.subTest(kind=kind, source_format=source_format), tempfile.TemporaryDirectory() as directory:
                    manifest, path, source_path = self.file_manifest(Path(directory), kind, source_format)
                    report = assess_measurements(path)
                    self.assertEqual(report['status'], 'PASS')
                    summary = next(source for source in report['sources'] if source['kind'] == kind)
                    self.assertEqual(summary['sha256'], hashlib.sha256(source_path.read_bytes()).hexdigest())
                    self.assertEqual(summary['record_count'], 1 if kind == 'electrical' else 12)

    def test_duplicate_json_keys_nonfinite_and_malformed_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manifest, path, source_path = self.file_manifest(base)
            original_manifest, original_source = path.read_text(), source_path.read_text()
            for replacement in ('{"schema_version":1,"schema_version":1}',
                                original_manifest.replace('"schema_version": 1', '"schema_version": NaN'),
                                original_manifest.replace('"schema_version": 1', '"schema_version": Infinity'),
                                '{', '[]'):
                path.write_text(replacement)
                self.assert_insufficient(path, 'INVALID_INPUT')
            path.write_text(original_manifest)
            for replacement in (original_source.replace('"errors": 0', '"errors": 0, "errors": 0'),
                                original_source.replace('"end_s": 2', '"end_s": NaN'),
                                original_source.replace('"schema_version": 1', '"schema_version": true')):
                source_path.write_text(replacement)
                self.assert_insufficient(path, 'INVALID_INPUT')
            source_path.write_bytes(b'\xff\xfe')
            self.assert_insufficient(path, 'INVALID_INPUT')
            source_path.unlink()
            self.assert_insufficient(path, 'INVALID_INPUT')

    def test_json_artifact_provenance_is_checked_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            _, path, source_path = self.file_manifest(Path(directory))
            original = json.loads(source_path.read_text())
            for field, value in (('origin', 'synthetic'), ('capture_id', 'different')):
                document = dict(original, **{field: value})
                source_path.write_text(json.dumps(document))
                report = self.assert_insufficient(path, 'SOURCE_PROVENANCE')
                self.assertEqual(report['numeric_status'], 'PASS')

    def test_csv_duplicate_missing_surplus_fields_and_nondecimal_counts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, path, source_path = self.file_manifest(Path(directory), source_format='csv')
            original = source_path.read_text()
            header, row = original.strip().splitlines()
            values = row.split(',')
            errors_index, bits_index = header.split(',').index('errors'), header.split(',').index('trial_bits')
            malformed = [header + ',errors\n' + row + ',0\n',
                         header.replace('errors', 'trial_bits') + '\n' + row + '\n',
                         header + '\n' + ','.join(values[:-1]) + '\n',
                         header + '\n' + row + ',extra\n']
            for index, value in ((errors_index, 'true'), (errors_index, '0.0'),
                                 (bits_index, '1e4'), (bits_index, '-1'), (bits_index, 'NaN')):
                changed = list(values)
                changed[index] = value
                malformed.append(header + '\n' + ','.join(changed) + '\n')
            for content in malformed:
                source_path.write_text(content)
                self.assert_insufficient(path, 'INVALID_INPUT')

    def test_source_paths_modes_and_resource_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manifest, path, source_path = self.file_manifest(base)
            for value in ('../outside.json', str(source_path.resolve()), 'missing.json'):
                changed = copy.deepcopy(manifest)
                changed['sources']['electrical']['path'] = value
                path.write_text(json.dumps(changed))
                self.assert_insufficient(path, 'INVALID_INPUT')
            path.write_text(json.dumps(manifest))
            for constant, limit in (('MAX_MANIFEST_BYTES', 10), ('MAX_FILE_BYTES', 10), ('MAX_ROWS', 0)):
                with mock.patch('sim.physical_validation.' + constant, limit):
                    self.assert_insufficient(path, 'INVALID_INPUT')
            manifest['sources']['electrical']['records'] = []
            self.assert_insufficient(manifest, 'INVALID_INPUT')

    def test_cli_exit_codes_and_json_report_for_pass_fail_and_insufficient(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path, output = base / 'manifest.json', base / 'nested' / 'report.json'
            for expected, mutate in ((0, lambda m: None),
                                     (1, lambda m: m['sources']['electrical']['records'][0].update(errors=100)),
                                     (2, lambda m: m['evidence'].update(origin='synthetic'))):
                manifest = declared_measured_fixture()
                mutate(manifest)
                path.write_text(json.dumps(manifest))
                run = subprocess.run([sys.executable, '-m', 'sim.physical_validation',
                                      '--manifest', str(path), '--output', str(output)],
                                     cwd=ROOT, text=True, capture_output=True, check=False)
                with self.subTest(expected=expected):
                    self.assertEqual(run.returncode, expected, run.stderr)
                    report = json.loads(output.read_text())
                    self.assertIn(report['status'], run.stdout)
                    self.assertEqual({'PASS': 0, 'FAIL': 1, 'INSUFFICIENT_EVIDENCE': 2}[report['status']], expected)
            path.unlink()
            run = subprocess.run([sys.executable, '-m', 'sim.physical_validation',
                                  '--manifest', str(path), '--output', str(output)],
                                 cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(json.loads(output.read_text())['status'], 'INSUFFICIENT_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
