"""
Test script for Server4 Analysis Engine.
Tests file discovery, parsing, and analysis without requiring MCP setup.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from server4.analysis_engine import (
    discover_files,
    categorize_files,
    read_file_content,
    parse_csv_file,
    parse_bed_file,
    generate_analysis_summary,
    get_job_work_dir
)
from server4.config import AGOUTIC_WORK_DIR


def test_file_discovery():
    """Test basic file discovery."""
    print("=" * 60)
    print("TEST 1: File Discovery")
    print("=" * 60)
    
    # Check if work directory exists
    print(f"\nWork directory: {AGOUTIC_WORK_DIR}")
    print(f"Exists: {AGOUTIC_WORK_DIR.exists()}")
    
    if not AGOUTIC_WORK_DIR.exists():
        print("❌ Work directory not found. Please set AGOUTIC_WORK_DIR.")
        return False
    
    # List job directories
    job_dirs = [d for d in AGOUTIC_WORK_DIR.iterdir() if d.is_dir()]
    print(f"\nFound {len(job_dirs)} job directories:")
    for job_dir in job_dirs[:5]:  # Show first 5
        print(f"  - {job_dir.name}")
    
    if not job_dirs:
        print("❌ No job directories found.")
        return False
    
    # Test discovery on first job
    test_uuid = job_dirs[0].name
    print(f"\n🔍 Testing discovery on job: {test_uuid}")
    
    try:
        file_listing = discover_files(test_uuid)
        print(f"✅ Found {file_listing.file_count} files")
        print(f"   Total size: {file_listing.total_size / (1024*1024):.2f} MB")
        
        # Show first 5 files
        print("\n   First 5 files:")
        for file_info in file_listing.files[:5]:
            print(f"   - {file_info.path} ({file_info.size} bytes)")
        
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_file_categorization():
    """Test file categorization."""
    print("\n" + "=" * 60)
    print("TEST 2: File Categorization")
    print("=" * 60)
    
    job_dirs = [d for d in AGOUTIC_WORK_DIR.iterdir() if d.is_dir()]
    if not job_dirs:
        print("❌ No job directories found.")
        return False
    
    test_uuid = job_dirs[0].name
    print(f"\n🗂️  Categorizing files for job: {test_uuid}")
    
    try:
        file_summary = categorize_files(test_uuid)
        print(f"✅ Categorization complete:")
        print(f"   TXT files: {len(file_summary.txt_files)}")
        print(f"   CSV files: {len(file_summary.csv_files)}")
        print(f"   BED files: {len(file_summary.bed_files)}")
        print(f"   Other files: {len(file_summary.other_files)}")
        
        # Show CSV files
        if file_summary.csv_files:
            print("\n   CSV files found:")
            for csv_file in file_summary.csv_files[:3]:
                print(f"   - {csv_file.name}")
        
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_csv_parsing():
    """Test CSV file parsing."""
    print("\n" + "=" * 60)
    print("TEST 3: CSV Parsing")
    print("=" * 60)
    
    job_dirs = [d for d in AGOUTIC_WORK_DIR.iterdir() if d.is_dir()]
    if not job_dirs:
        print("❌ No job directories found.")
        return False
    
    test_uuid = job_dirs[0].name
    
    try:
        file_summary = categorize_files(test_uuid)
        
        if not file_summary.csv_files:
            print("⚠️  No CSV files found in this job.")
            return True  # Not an error, just no CSV files
        
        # Parse first CSV file
        csv_file = file_summary.csv_files[0]
        print(f"\n📊 Parsing CSV: {csv_file.name}")
        
        parsed_data = parse_csv_file(test_uuid, csv_file.path, max_rows=5)
        print(f"✅ Parsed successfully:")
        print(f"   Columns: {parsed_data.columns}")
        print(f"   Total rows: {parsed_data.row_count}")
        print(f"   Preview rows: {parsed_data.preview_rows}")
        
        if parsed_data.data:
            print("\n   First row:")
            for key, value in list(parsed_data.data[0].items())[:5]:
                print(f"   - {key}: {value}")
        
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_bed_parsing():
    """Test BED file parsing."""
    print("\n" + "=" * 60)
    print("TEST 4: BED Parsing")
    print("=" * 60)
    
    job_dirs = [d for d in AGOUTIC_WORK_DIR.iterdir() if d.is_dir()]
    if not job_dirs:
        print("❌ No job directories found.")
        return False
    
    test_uuid = job_dirs[0].name
    
    try:
        file_summary = categorize_files(test_uuid)
        
        if not file_summary.bed_files:
            print("⚠️  No BED files found in this job.")
            return True  # Not an error, just no BED files
        
        # Parse first BED file
        bed_file = file_summary.bed_files[0]
        print(f"\n🧬 Parsing BED: {bed_file.name}")
        
        parsed_data = parse_bed_file(test_uuid, bed_file.path, max_records=5)
        print(f"✅ Parsed successfully:")
        print(f"   Total records: {parsed_data.record_count}")
        print(f"   Preview records: {parsed_data.preview_records}")
        
        if parsed_data.records:
            print("\n   First record:")
            first = parsed_data.records[0]
            print(f"   - chrom: {first.chrom}")
            print(f"   - chromStart: {first.chromStart}")
            print(f"   - chromEnd: {first.chromEnd}")
            print(f"   - name: {first.name}")
            print(f"   - score: {first.score}")
            print(f"   - strand: {first.strand}")
        
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_analysis_summary():
    """Test comprehensive analysis summary."""
    print("\n" + "=" * 60)
    print("TEST 5: Analysis Summary")
    print("=" * 60)
    
    job_dirs = [d for d in AGOUTIC_WORK_DIR.iterdir() if d.is_dir()]
    if not job_dirs:
        print("❌ No job directories found.")
        return False
    
    test_uuid = job_dirs[0].name
    print(f"\n📋 Generating summary for job: {test_uuid}")
    
    try:
        summary = generate_analysis_summary(test_uuid)
        print(f"✅ Summary generated:")
        print(f"   Sample: {summary.sample_name}")
        print(f"   Workflow: {summary.workflow_type}")
        print(f"   Status: {summary.status}")
        print(f"   Work dir: {summary.work_dir}")
        print(f"\n   File counts:")
        print(f"   - TXT: {len(summary.file_summary.txt_files)}")
        print(f"   - CSV: {len(summary.file_summary.csv_files)}")
        print(f"   - BED: {len(summary.file_summary.bed_files)}")
        print(f"   - Other: {len(summary.file_summary.other_files)}")
        
        if summary.key_results:
            print(f"\n   Key results: {summary.key_results}")
        
        if summary.parsed_reports:
            print(f"\n   Parsed reports: {list(summary.parsed_reports.keys())}")
        
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Server4 Analysis Engine Test Suite")
    print("=" * 60)
    
    tests = [
        test_file_discovery,
        test_file_categorization,
        test_csv_parsing,
        test_bed_parsing,
        test_analysis_summary
    ]
    
    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append((test_func.__name__, result))
        except Exception as e:
            print(f"\n❌ Test {test_func.__name__} failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_func.__name__, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
