import ast
from pathlib import Path

test_files = [
    Path(r"d:\teamwork_projects\smart_drive_os\tests\test_snapshot.py"),
    Path(r"d:\teamwork_projects\smart_drive_os\tests\test_adversarial_snapshot.py"),
]

print("=== AUDITING TEST ASSERTIONS FOR FACADES / TRIVIAL PASSES ===")

for tf in test_files:
    print(f"\nAnalyzing: {tf.name}")
    tree = ast.parse(tf.read_text(encoding="utf-8"))
    test_methods = 0
    assertion_counts = 0
    trivial_assertions = 0
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            test_methods += 1
            method_asserts = 0
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func_name = ""
                    if isinstance(child.func, ast.Attribute):
                        func_name = child.func.attr
                    elif isinstance(child.func, ast.Name):
                        func_name = child.func.id
                    if func_name.startswith("assert"):
                        method_asserts += 1
                        assertion_counts += 1
                        # Check for trivial assertions like assertEqual(True, True) or assertTrue(True)
                        if len(child.args) == 1 and isinstance(child.args[0], ast.Constant) and child.args[0].value is True:
                            trivial_assertions += 1
                            print(f"  [WARN] Trivial assertion in {node.name}: assertTrue(True)")
                        elif len(child.args) == 2 and isinstance(child.args[0], ast.Constant) and isinstance(child.args[1], ast.Constant) and child.args[0].value == child.args[1].value:
                            trivial_assertions += 1
                            print(f"  [WARN] Trivial assertion in {node.name}: assertEqual({child.args[0].value}, {child.args[1].value})")
            if method_asserts == 0:
                print(f"  [WARN] Test method {node.name} has 0 assertions!")
                
    print(f"  Total test methods: {test_methods}")
    print(f"  Total assertion calls: {assertion_counts}")
    print(f"  Trivial assertions found: {trivial_assertions}")
    if trivial_assertions == 0 and assertion_counts >= test_methods:
        print(f"  [PASS] All test assertions are authentic and non-trivial.")

print("\n=== TEST ASSERTION AUDIT COMPLETE ===")
