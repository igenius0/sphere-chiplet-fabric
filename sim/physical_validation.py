"""Strict, dependency-free qualification of declared physical measurements.

This is an evidence-ingestion gate, not a device simulator or a compliance
certifier. See docs/PHYSICAL_VALIDATION_PROTOCOL.md for the schema and limits.
"""
import argparse
import csv
from datetime import datetime, timedelta
from decimal import (Context, Decimal, DecimalException, localcontext,
                     MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN)
import hashlib
import io
import json
import math
from pathlib import Path
import re


VERSION = '1.2.0'
PASS, FAIL, INSUFFICIENT = 'PASS', 'FAIL', 'INSUFFICIENT_EVIDENCE'
ORIGINS = ('measured', 'synthetic', 'simulated', 'none')
QUANTITIES = ('temperature_c', 'power_w', 'voltage_v', 'coolant_c')
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ROWS = 100000
MAX_PROFILES = 1024
MAX_COUNT = 10 ** 18
MAX_BINOMIAL_TERMS = 10001
MAX_TOTAL_BINOMIAL_TERMS = 100000
MAX_NUMBER = 10 ** 12
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
_COUNT = re.compile(r'[0-9]{1,19}\Z')
_COMMON = {'corner', 'origin', 'capture_id', 'instrument_id'}
_ELECTRICAL = _COMMON | {'lane', 'start_s', 'end_s', 'errors', 'trial_bits'}
_TELEMETRY = _COMMON | {'quantity', 'channel', 'timestamp_s', 'value'}


class _InputError(ValueError):
    pass


def _keys(value, required, optional=(), name='object'):
    if not isinstance(value, dict):
        raise _InputError(f'{name} must be an object')
    if not all(isinstance(key, str) for key in value):
        raise _InputError(f'{name} keys must be strings')
    missing = sorted(set(required) - value.keys())
    unknown = sorted(value.keys() - set(required) - set(optional))
    if missing or unknown:
        raise _InputError(f'{name}: missing keys {missing}; unknown keys {unknown}')


def _string(value, name):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > 256 or any(ord(char) < 32 for char in value)):
        raise _InputError(f'{name} must be a nonempty trimmed string of <=256 characters')
    return value


def _number(value, name, minimum=-MAX_NUMBER, maximum=MAX_NUMBER):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)):
        raise _InputError(f'{name} must be a finite number in [{minimum}, {maximum}]')
    return float(value)


def _count(value, name):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise _InputError(f'{name} must be an exact integer in [0, {MAX_COUNT}]')
    return value


def _names(value, name):
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        raise _InputError(f'{name} must contain 1..256 unique names')
    names = [_string(item, name) for item in value]
    if len(set(names)) != len(names):
        raise _InputError(f'{name} must contain unique names')
    return sorted(names)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InputError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _reject_constant(value):
    raise _InputError(f'nonfinite JSON number: {value}')


def _json_load(raw):
    try:
        return json.loads(raw, object_pairs_hook=_json_pairs, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise _InputError(f'invalid UTF-8 JSON: {error}') from error


def _read_bytes(path, limit):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise _InputError(f'{path.name} exceeds the {limit}-byte input limit')
    return raw


def _logadd(a, b):
    if a == -math.inf:
        return b
    if b > a:
        a, b = b, a
    return a + math.log1p(math.exp(b - a))


def _log_cdf(k, n, p, log_combination):
    """Log Pr[X <= k], summing a binomial tail without forming n! or p**n."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return -math.inf
    log_p, log_q = math.log(p), math.log1p(-p)
    term = log_combination + k * log_p + (n - k) * log_q
    total = term
    for j in range(k, 0, -1):
        term += math.log(j) - math.log(n - j + 1) + log_q - log_p
        total = _logadd(total, term)
    return min(0.0, total)


def _high_precision_upper_bound(n, k, confidence, complement):
    """Resolve extreme CDF targets without losing a tiny tail probability.

    This uncommon path retains the same finite binomial sum and work limit.
    Extra decimal digits resolve the requested confidence even at the smallest
    positive float; float bisection stops at adjacent representable endpoints.
    """
    tail_probability = min(confidence, 1 - confidence)
    precision = max(60, math.ceil(-math.log10(tail_probability)) + 50)
    # Own the rounding/precision policy rather than inheriting caller context.
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN,
                              Emax=MAX_EMAX, Emin=MIN_EMIN)):
        confidence = Decimal.from_float(confidence)
        target = confidence if complement else 1 - confidence
        combination = Decimal(math.comb(n, k))
        low, high = 0.0, 1.0
        for _ in range(1200):
            middle = low + (high - low) / 2
            if middle == low or middle == high:
                break
            probability = Decimal.from_float(middle)
            p = 1 - probability if complement else probability
            q = 1 - p
            term = combination * p ** k * q ** (n - k)
            total = term
            for j in range(k, 0, -1):
                term = term * j * q / ((n - j + 1) * p)
                total += term
            below_root = total < target if complement else total > target
            if below_root:
                low = middle
            else:
                high = middle
        return high


def binomial_upper_bound(errors, trial_bits, confidence_level=0.95):
    """One-sided exact Clopper-Pearson upper BER limit (independent bits).

    Zero errors use -expm1(log1p(-confidence)/N), avoiding cancellation.
    Nonzero errors invert a log-binomial CDF by bisection. The returned upper
    floating-point bracket resolves the root, subject to arithmetic rounding.
    Extreme confidence uses higher precision to resolve a near-one CDF.
    Inputs exceeding the documented work limit are rejected, not approximated.
    """
    errors = _count(errors, 'errors')
    n = _count(trial_bits, 'trial_bits')
    confidence = _number(confidence_level, 'confidence_level', 0, 1)
    if not 0 < confidence < 1 or n == 0 or errors > n:
        raise _InputError('require 0 < confidence_level < 1 and 0 <= errors <= trial_bits > 0')
    if errors == n:
        return 1.0
    if errors == 0:
        return max(math.nextafter(0.0, 1.0), -math.expm1(math.log1p(-confidence) / n))
    # Near all errors, use Pr[n-X <= n-errors-1] to avoid a long lower tail.
    complement = errors > (n - 1) // 2
    k = n - errors - 1 if complement else errors
    if k + 1 > MAX_BINOMIAL_TERMS:
        raise _InputError(f'exact binomial tail requires more than {MAX_BINOMIAL_TERMS} terms')
    if min(confidence, 1 - confidence) < .01:
        try:
            return _high_precision_upper_bound(n, k, confidence, complement)
        except DecimalException as error:
            raise _InputError('exact binomial tail exceeds supported decimal arithmetic') from error
    # fsum of log factors is stable when n is large and k is small; subtracting
    # lgamma(n+1)-lgamma(n-k+1) would lose significant digits in that case.
    log_combination = math.fsum(math.log(n - i) - math.log(i + 1) for i in range(k))
    target = math.log(confidence) if complement else math.log1p(-confidence)
    low, high = 0.0, 1.0
    # 200 halvings also resolve BER near 1e-18 without an absolute tolerance.
    for _ in range(200):
        middle = low + (high - low) / 2
        if middle == low or middle == high:
            break
        if complement:
            below_root = _log_cdf(k, n, 1 - middle, log_combination) < target
        else:
            below_root = _log_cdf(k, n, middle, log_combination) > target
        if below_root:
            low = middle
        else:
            high = middle
    return high


def _configuration(value):
    required = {'lanes', 'corners', 'rails', 'thermal_channels', 'coolant_channels',
                'min_duration_s', 'max_sample_gap_s', 'confidence_level', 'limits'}
    _keys(value, required, name='configuration')
    config = {key: _names(value[key], key) for key in
              ('lanes', 'corners', 'rails', 'thermal_channels', 'coolant_channels')}
    config['min_duration_s'] = _number(value['min_duration_s'], 'min_duration_s', 0)
    config['max_sample_gap_s'] = _number(value['max_sample_gap_s'], 'max_sample_gap_s', 0)
    config['confidence_level'] = _number(value['confidence_level'], 'confidence_level', 0, 1)
    if (config['min_duration_s'] <= 0 or config['max_sample_gap_s'] <= 0
            or not 0 < config['confidence_level'] < 1):
        raise _InputError('duration and gap must be positive; confidence_level must be in (0,1)')
    expected = len(config['corners']) * (len(config['lanes'])
               + len(config['thermal_channels']) + 2 * len(config['rails'])
               + len(config['coolant_channels']))
    if expected > MAX_PROFILES:
        raise _InputError(f'configuration exceeds {MAX_PROFILES} lane/channel-corner profiles')
    limits = value['limits']
    _keys(limits, {'ber_upper_max'} | set(QUANTITIES), name='limits')
    result = {'ber_upper_max': _number(limits['ber_upper_max'], 'ber_upper_max', 0, 1)}
    for quantity, names in _channels(config).items():
        _keys(limits[quantity], names, name=f'limits.{quantity}')
        result[quantity] = {}
        for channel in names:
            bounds = limits[quantity][channel]
            _keys(bounds, {'min', 'max'}, name=f'limits.{quantity}.{channel}')
            minimum = _number(bounds['min'], 'range min')
            maximum = _number(bounds['max'], 'range max')
            if minimum > maximum:
                raise _InputError('range min must not exceed range max')
            result[quantity][channel] = {'min': minimum, 'max': maximum}
    config['limits'] = result
    return config


def _channels(config):
    return {'temperature_c': config['thermal_channels'], 'power_w': config['rails'],
            'voltage_v': config['rails'], 'coolant_c': config['coolant_channels']}


def _source(source, kind, base, manifest_origin, capture_id, add_issue):
    _keys(source, {'origin', 'capture_id'}, {'records', 'path', 'format'}, f'sources.{kind}')
    origin = _string(source['origin'], 'source origin')
    capture = _string(source['capture_id'], 'source capture_id')
    if origin not in ORIGINS:
        raise _InputError('source origin must be measured, synthetic, simulated or none')
    if origin != 'measured' or origin != manifest_origin or capture != capture_id:
        add_issue('qualification', 'SOURCE_PROVENANCE', kind,
                  'Source provenance is not matching declared measured evidence.')
    if 'records' in source:
        if 'path' in source or 'format' in source:
            raise _InputError('a source must use records or path/format, not both')
        rows, source_format = source['records'], 'inline'
        try:
            raw = json.dumps(rows, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise _InputError('inline records must contain finite JSON values') from error
        if len(raw) > MAX_FILE_BYTES:
            raise _InputError('inline source exceeds input byte limit')
    else:
        if set(source) != {'origin', 'capture_id', 'path', 'format'}:
            raise _InputError('a file source requires path and format')
        source_format = source['format']
        relative = Path(_string(source['path'], 'source path'))
        path = (base / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(base):
            raise _InputError('source paths must remain within the manifest directory')
        raw = _read_bytes(path, MAX_FILE_BYTES)
        if source_format == 'json':
            document = _json_load(raw)
            _keys(document, {'schema_version', 'origin', 'capture_id', 'records'}, name='source JSON')
            if type(document['schema_version']) is not int or document['schema_version'] != 1:
                raise _InputError('source JSON schema_version must be integer 1')
            if document['origin'] != origin or document['capture_id'] != capture:
                add_issue('qualification', 'SOURCE_PROVENANCE', kind,
                          'JSON artifact provenance does not match its source descriptor.')
            rows = document['records']
        elif source_format == 'csv':
            try:
                reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
                expected = _ELECTRICAL if kind == 'electrical' else _TELEMETRY
                if (reader.fieldnames is None or len(reader.fieldnames) != len(expected)
                        or set(reader.fieldnames) != expected):
                    raise _InputError(f'{kind} CSV must have exactly these unique columns: {sorted(expected)}')
                rows = []
                for row in reader:
                    if len(rows) >= MAX_ROWS:
                        raise _InputError(f'source exceeds {MAX_ROWS} records')
                    rows.append(row)
            except (UnicodeError, csv.Error) as error:
                raise _InputError(f'invalid CSV: {error}') from error
        else:
            raise _InputError('file source format must be csv or json')
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise _InputError(f'source records must be a list of at most {MAX_ROWS} rows')
    if not rows:
        add_issue('coverage', 'EMPTY_SOURCE', kind, 'Source contains no measurement records.')
    return rows, {'kind': kind, 'format': source_format, 'origin': origin,
                  'capture_id': capture, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'record_count': len(rows)}, source_format == 'csv'


def _record(row, kind, from_csv, config, source, add_issue):
    expected = _ELECTRICAL if kind == 'electrical' else _TELEMETRY
    _keys(row, expected, name=f'{kind} record')
    record = dict(row)
    for key in expected - {'start_s', 'end_s', 'errors', 'trial_bits', 'timestamp_s', 'value'}:
        record[key] = _string(row[key], key)
    if record['origin'] not in ORIGINS:
        raise _InputError('record origin must be measured, synthetic, simulated or none')
    if (record['origin'] != 'measured' or record['origin'] != source['origin']
            or record['capture_id'] != source['capture_id']):
        add_issue('qualification', 'RECORD_PROVENANCE', kind,
                  'Record provenance is not matching declared measured source evidence.')
    if record['corner'] not in config['corners']:
        raise _InputError('record contains an unconfigured corner')
    numeric = ('start_s', 'end_s', 'errors', 'trial_bits') if kind == 'electrical' else ('timestamp_s', 'value')
    for key in numeric:
        value = row[key]
        if from_csv:
            if not isinstance(value, str):
                raise _InputError('CSV record has missing or surplus fields')
            value = value.strip()
            if key in ('errors', 'trial_bits'):
                if not _COUNT.fullmatch(value):
                    raise _InputError(f'{key} CSV count must be a decimal integer')
                value = int(value)
            else:
                if len(value) > 64 or not _DECIMAL.fullmatch(value):
                    raise _InputError(f'{key} CSV field must be a finite decimal number')
                value = float(value)
        record[key] = (_count(value, key) if key in ('errors', 'trial_bits') else
                       _number(value, key, -MAX_NUMBER if key == 'value' else 0))
    if kind == 'electrical':
        if record['lane'] not in config['lanes']:
            raise _InputError('record contains an unconfigured lane')
        if record['end_s'] <= record['start_s'] or record['errors'] > record['trial_bits']:
            raise _InputError('electrical record requires end_s > start_s and errors <= trial_bits')
    elif (record['quantity'] not in QUANTITIES or
          record['channel'] not in _channels(config)[record['quantity']]):
        raise _InputError('record contains an unconfigured quantity/channel')
    return record


def _date(value, name):
    text = _string(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as error:
        raise _InputError(f'{name} must be an ISO-8601 timestamp') from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _InputError(f'{name} must include a timezone offset')
    return parsed


def _qualify(manifest, instrument_quantities, instrument_end_s, add_issue):
    metadata = {'identity': {}, 'evidence': {}, 'instruments': []}
    identity = manifest.get('identity', {})
    try:
        _keys(identity, {'dut_id', 'dut_revision', 'board_id', 'operator', 'laboratory'}, name='identity')
        for key, value in identity.items():
            _string(value, f'identity.{key}')
        metadata['identity'] = dict(identity)
    except _InputError as error:
        add_issue('qualification', 'IDENTITY_INCOMPLETE', 'identity', str(error))
    evidence = manifest.get('evidence', {})
    captured_at = None
    try:
        _keys(evidence, {'origin', 'capture_id', 'captured_at', 'description'}, name='evidence')
        for key, value in evidence.items():
            _string(value, f'evidence.{key}')
        captured_at = _date(evidence['captured_at'], 'captured_at')
        metadata['evidence'] = dict(evidence)
        if evidence['origin'] != 'measured':
            add_issue('qualification', 'NOT_MEASURED', 'evidence',
                      'Synthetic, simulated, absent or undeclared hardware evidence cannot qualify.')
    except _InputError as error:
        add_issue('qualification', 'EVIDENCE_INCOMPLETE', 'evidence', str(error))
    instruments = manifest.get('instruments', [])
    known = {}
    if not isinstance(instruments, list) or len(instruments) > 256:
        add_issue('qualification', 'CALIBRATION_INCOMPLETE', 'instruments',
                  'instruments must be a list of at most 256 calibration records.')
        instruments = []
    for index, instrument in enumerate(instruments):
        try:
            _keys(instrument, {'instrument_id', 'serial_number', 'calibration_id',
                              'calibrated_at', 'valid_until', 'uncertainty', 'uncertainty_method'},
                  name='instrument')
            for key in ('instrument_id', 'serial_number', 'calibration_id', 'uncertainty_method'):
                _string(instrument[key], key)
            instrument_id = instrument['instrument_id']
            if instrument_id in known:
                raise _InputError('instrument_id must be unique')
            first = _date(instrument['calibrated_at'], 'calibrated_at')
            last = _date(instrument['valid_until'], 'valid_until')
            end_at = (captured_at + timedelta(seconds=instrument_end_s.get(instrument_id, 0))
                      if captured_at is not None else None)
            if (first > last or captured_at is None or not first <= captured_at <= last
                    or end_at > last):
                raise _InputError('capture timestamp must be within the instrument calibration interval')
            uncertainty = instrument['uncertainty']
            _keys(uncertainty, instrument_quantities.get(instrument_id, set()),
                  set(QUANTITIES) | {'ber'}, name='uncertainty')
            for key, value in uncertainty.items():
                _number(value, f'uncertainty.{key}', 0)
            known[instrument_id] = instrument
            metadata['instruments'].append(dict(instrument))
        except (ValueError, OverflowError) as error:
            add_issue('qualification', 'CALIBRATION_INCOMPLETE', f'instruments[{index}]', str(error))
    for instrument_id in sorted(instrument_quantities):
        if instrument_id not in known:
            add_issue('qualification', 'UNCALIBRATED_INSTRUMENT', instrument_id,
                      'A used instrument has no complete valid calibration and uncertainty record.')
    if not instrument_quantities:
        add_issue('qualification', 'NO_INSTRUMENT_EVIDENCE', 'instruments',
                  'No valid measurement records identify a calibrated instrument.')
    metadata['instruments'].sort(key=lambda item: item['instrument_id'])
    return metadata


def _assess_rows(rows, config, add_issue):
    electrical, telemetry, instruments, instrument_end_s = {}, {}, {}, {}
    for kind, records in rows.items():
        for record in records:
            instruments.setdefault(record['instrument_id'], set()).add('ber' if kind == 'electrical' else record['quantity'])
            instrument_end_s[record['instrument_id']] = max(
                instrument_end_s.get(record['instrument_id'], 0),
                record['end_s'] if kind == 'electrical' else record['timestamp_s'])
            key = ((record['corner'], record['lane']) if kind == 'electrical' else
                   (record['corner'], record['quantity'], record['channel']))
            (electrical if kind == 'electrical' else telemetry).setdefault(key, []).append(record)
    electrical_results, telemetry_results = [], []
    total_terms = 0
    duration, gap_limit = config['min_duration_s'], config['max_sample_gap_s']
    for corner in config['corners']:
        for lane in config['lanes']:
            scope = f'{corner}/lane/{lane}'
            group = electrical.get((corner, lane), [])
            result = {'corner': corner, 'lane': lane, 'status': INSUFFICIENT,
                      'interval_count': len(group)}
            electrical_results.append(result)
            if not group:
                add_issue('coverage', 'MISSING_LANE_CORNER', scope, 'No electrical measurements for required lane/corner.')
                continue
            for previous, current in zip(group, group[1:]):
                if current['start_s'] < previous['end_s']:
                    raise _InputError(f'{scope}: electrical intervals must be ordered and nonoverlapping')
            errors = sum(row['errors'] for row in group)
            n = sum(row['trial_bits'] for row in group)
            _count(errors, 'aggregate errors')
            _count(n, 'aggregate trial_bits')
            active = [row for row in group if row['trial_bits'] > 0]
            max_gap = max((b['start_s'] - a['end_s'] for a, b in zip(active, active[1:])), default=0.0)
            observed = math.fsum(row['end_s'] - row['start_s'] for row in active)
            coverage = (bool(active) and active[0]['start_s'] == 0
                        and active[-1]['end_s'] >= duration
                        and observed >= duration and max_gap <= gap_limit)
            result.update(errors=errors, trial_bits=n, start_s=group[0]['start_s'],
                          end_s=group[-1]['end_s'], observed_duration_s=observed, max_gap_s=max_gap)
            if not coverage:
                add_issue('coverage', 'ELECTRICAL_COVERAGE', scope,
                          'Require start at 0, minimum observed duration, bounded gaps and positive trial bits.')
            if n:
                total_terms += 1 if errors in (0, n) else min(errors + 1, n - errors)
                if total_terms > MAX_TOTAL_BINOMIAL_TERMS:
                    raise _InputError('aggregate exact-binomial arithmetic work limit exceeded')
                upper = binomial_upper_bound(errors, n, config['confidence_level'])
                failed = upper > config['limits']['ber_upper_max']
                result.update(observed_ber=errors / n, ber_upper_bound=upper,
                              confidence_level=config['confidence_level'],
                              ber_upper_max=config['limits']['ber_upper_max'])
                result['status'] = FAIL if failed else PASS if coverage else INSUFFICIENT
                if failed:
                    add_issue('failure', 'BER_LIMIT', scope, 'One-sided upper BER bound exceeds configured maximum.')
        for quantity, channels in _channels(config).items():
            for channel in channels:
                scope = f'{corner}/{quantity}/{channel}'
                group = telemetry.get((corner, quantity, channel), [])
                result = {'corner': corner, 'quantity': quantity, 'channel': channel,
                          'status': INSUFFICIENT, 'sample_count': len(group)}
                telemetry_results.append(result)
                if not group:
                    add_issue('coverage', 'MISSING_CHANNEL_CORNER', scope,
                              'No samples for required quantity/channel/corner.')
                    continue
                times = [row['timestamp_s'] for row in group]
                if any(b <= a for a, b in zip(times, times[1:])):
                    raise _InputError(f'{scope}: timestamps must be supplied in strictly increasing order')
                max_gap = max((b - a for a, b in zip(times, times[1:])), default=0.0)
                coverage = len(group) >= 2 and times[0] == 0 and times[-1] >= duration and max_gap <= gap_limit
                minimum = min(row['value'] for row in group)
                maximum = max(row['value'] for row in group)
                limits = config['limits'][quantity][channel]
                failed = minimum < limits['min'] or maximum > limits['max']
                result.update(start_s=times[0], end_s=times[-1], max_gap_s=max_gap,
                              observed_min=minimum, observed_max=maximum, limits=limits,
                              status=FAIL if failed else PASS if coverage else INSUFFICIENT)
                if not coverage:
                    add_issue('coverage', 'TELEMETRY_COVERAGE', scope,
                              'Require at least two samples covering 0..min_duration_s with bounded gaps.')
                if failed:
                    add_issue('failure', 'RANGE_LIMIT', scope, 'A recorded sample exceeds its configured range.')
    return electrical_results, telemetry_results, instruments, instrument_end_s


def assess_measurements(manifest):
    """Return a deterministic JSON-safe report for a dict or JSON manifest path.

    Relative sources resolve beside a manifest file, or beneath cwd for dict
    input. Malformed/missing input is reported as insufficient evidence. Numeric
    violations remain separately visible when provenance cannot qualify.
    """
    report = {'report_version': VERSION, 'schema_version': 1, 'status': INSUFFICIENT,
              'numeric_status': INSUFFICIENT, 'evidence_qualification': 'UNQUALIFIED',
              'electrical': [], 'telemetry': [], 'sources': [], 'issues': [],
              'scope': 'Declared measurement evidence against user-configured limits; no compliance certification.',
              'assumptions': ['BER bounds assume independent identically distributed bit trials.',
                              'Instrument uncertainty is recorded, not automatically guard-banded.',
                              'Provenance assertions and calibration authenticity require independent review.']}
    categories = set()
    seen_issues = set()

    def add_issue(category, code, scope, message):
        categories.add(category)
        item = {'category': category, 'code': code, 'scope': scope, 'message': message}
        token = (category, code, scope, message)
        if token not in seen_issues:
            report['issues'].append(item)
            seen_issues.add(token)

    loaded = None
    instrument_quantities = {}
    instrument_end_s = {}
    try:
        if isinstance(manifest, (str, Path)):
            path = Path(manifest).resolve()
            loaded = _json_load(_read_bytes(path, MAX_MANIFEST_BYTES))
            base = path.parent
        else:
            loaded, base = manifest, Path.cwd().resolve()
        _keys(loaded, {'schema_version', 'configuration'},
              {'identity', 'evidence', 'instruments', 'sources'}, name='manifest')
        if type(loaded['schema_version']) is not int or loaded['schema_version'] != 1:
            raise _InputError('manifest schema_version must be integer 1')
        config = _configuration(loaded['configuration'])
        report['configuration'] = config
        evidence = loaded.get('evidence', {})
        origin = evidence.get('origin') if isinstance(evidence, dict) else None
        capture_id = evidence.get('capture_id') if isinstance(evidence, dict) else None
        sources = loaded.get('sources', {})
        _keys(sources, set(), {'electrical', 'telemetry'}, name='sources')
        rows = {'electrical': [], 'telemetry': []}
        for kind in rows:
            if kind not in sources:
                add_issue('coverage', 'MISSING_SOURCE', kind, 'Required measurement source is absent.')
                continue
            raw_rows, summary, from_csv = _source(sources[kind], kind, base, origin, capture_id, add_issue)
            report['sources'].append(summary)
            rows[kind] = [_record(row, kind, from_csv, config, sources[kind], add_issue) for row in raw_rows]
        electrical, telemetry, instrument_quantities, instrument_end_s = _assess_rows(rows, config, add_issue)
        report['electrical'], report['telemetry'] = electrical, telemetry
    except (ValueError, TypeError, OSError, OverflowError, RecursionError) as error:
        add_issue('invalid_input', 'INVALID_INPUT', 'input', str(error))
    if isinstance(loaded, dict):
        report.update(_qualify(loaded, instrument_quantities, instrument_end_s, add_issue))
    else:
        add_issue('qualification', 'NO_MANIFEST', 'input', 'No valid measurement manifest was supplied.')
    report['numeric_status'] = (FAIL if 'failure' in categories else INSUFFICIENT
                                if categories & {'coverage', 'invalid_input'} else PASS)
    qualified = not categories & {'qualification', 'invalid_input'}
    report['evidence_qualification'] = 'QUALIFIED_MEASURED' if qualified else 'UNQUALIFIED'
    if qualified:
        report['status'] = report['numeric_status']
    report['issues'].sort(key=lambda item: (item['category'], item['scope'], item['code'], item['message']))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    report = assess_measurements(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    print(f"{report['status']} (numeric={report['numeric_status']}, evidence={report['evidence_qualification']})")
    return {PASS: 0, FAIL: 1, INSUFFICIENT: 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
