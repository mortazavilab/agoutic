import argparse
import collections
import gzip
import json
import sys
from pathlib import Path


def _strip_bed_suffix(file_name):
    lower_name = file_name.lower()
    if lower_name.endswith('.bed.gz'):
        return file_name[:-7]
    if lower_name.endswith('.bed'):
        return file_name[:-4]
    return file_name


def _open_bed_file(bed_path):
    if Path(bed_path).name.lower().endswith('.bed.gz'):
        return gzip.open(bed_path, 'rt', encoding='utf-8')
    return open(bed_path, 'r', encoding='utf-8')


def _parse_bed_metadata(bed_file):
    file_name = Path(bed_file).name
    stem = _strip_bed_suffix(file_name)
    parts = stem.split('.')

    metadata = {
        'sample': stem,
        'genome': 'unknown',
        'strand': '',
        'modification': '',
        'file_name': file_name,
        'file_path': str(Path(bed_file)),
    }
    if len(parts) >= 5 and parts[-1] == 'filtered' and parts[-3] in {'plus', 'minus'}:
        metadata['sample'] = '.'.join(parts[:-4]) or stem
        metadata['genome'] = parts[-4]
        metadata['strand'] = parts[-3]
        metadata['modification'] = parts[-2]
    return metadata


def count_chromosomes(bed_files):
    counts = collections.defaultdict(int)
    input_files = []

    for bed_file in bed_files:
        bed_path = Path(bed_file)
        metadata = _parse_bed_metadata(str(bed_path))
        input_files.append(metadata)

        try:
            with _open_bed_file(bed_path) as handle:
                for line in handle:
                    if line.startswith(('#', 'track', 'browser')) or not line.strip():
                        continue

                    chrom = line.split('\t', 1)[0]
                    key = (
                        metadata['sample'],
                        metadata['genome'],
                        metadata['modification'],
                        chrom,
                    )
                    counts[key] += 1
        except FileNotFoundError:
            print(f"Error: The file '{bed_file}' was not found.", file=sys.stderr)
            sys.exit(1)

    return counts, input_files


def build_dataframe(counts, input_files):
    rows = []
    for (sample, genome, modification, chrom), count in sorted(counts.items()):
        rows.append({
            'Sample': sample,
            'Genome': genome,
            'Modification': modification,
            'Chromosome': chrom,
            'Count': count,
        })

    return {
        'columns': ['Sample', 'Genome', 'Modification', 'Chromosome', 'Count'],
        'data': rows,
        'row_count': len(rows),
        'metadata': {
            'label': 'BED chromosome counts',
            'kind': 'bed_chromosome_counts',
            'aggregated_by': ['Sample', 'Genome', 'Modification', 'Chromosome'],
            'input_files': input_files,
        },
    }


def print_table(dataframe):
    columns = dataframe['columns']
    widths = {column: len(column) for column in columns}
    for row in dataframe['data']:
        for column in columns:
            widths[column] = max(widths[column], len(str(row[column])))

    header = ' | '.join(f"{column:<{widths[column]}}" for column in columns)
    separator = '-+-'.join('-' * widths[column] for column in columns)
    print(header)
    print(separator)
    for row in dataframe['data']:
        print(' | '.join(f"{str(row[column]):<{widths[column]}}" for column in columns))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Count BED or BED.GZ regions per chromosome, aggregating by sample, genome, and modification.',
    )
    parser.add_argument('--json', action='store_true', dest='as_json', help='Emit structured JSON output for downstream dataframe extraction.')
    parser.add_argument('bed_files', nargs='+', help='One or more .bed or .bed.gz files to count.')
    args = parser.parse_args(argv)

    counts, input_files = count_chromosomes(args.bed_files)
    dataframe = build_dataframe(counts, input_files)

    if args.as_json:
        print(json.dumps(dataframe))
        return 0

    print_table(dataframe)
    return 0


if __name__ == '__main__':
    sys.exit(main())
