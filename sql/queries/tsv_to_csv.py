import csv
import sys
import os

# Increase CSV field size limit to handle large fields
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    # On some systems, sys.maxsize is too large
    csv.field_size_limit(2147483647)  # Max int32 value

def convert_tsv_to_csv(tsv_file, csv_file):
    try:
        with open(tsv_file, 'r', encoding='utf-8') as fin:
            reader = csv.reader(fin, delimiter='\t')
            with open(csv_file, 'w', encoding='utf-8', newline='') as fout:
                writer = csv.writer(fout, quoting=csv.QUOTE_ALL)
                for row in reader:
                    writer.writerow(row)
        print(f"Successfully converted {tsv_file} to {csv_file}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 tsv_to_csv.py <input_file.tsv>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.")
        sys.exit(1)
        
    base, _ = os.path.splitext(input_file)
    output_file = base + ".csv"
    
    convert_tsv_to_csv(input_file, output_file)