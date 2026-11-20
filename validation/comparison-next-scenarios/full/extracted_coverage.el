// Candidate exclusions exported by Verdi NPI. Existing exclusions are not duplicated.
CHECKSUM: "2146436421 1988218020"
INSTANCE: fixture_tb
Branch 0 "3023540047" "(!$value$plusargs(\"mode=%d\", mode))" (0) "(!$value$plusargs(\"mode=%d\", mode)) 1"
CHECKSUM: "2146436421 2706505538"
INSTANCE: fixture_tb
Block 4 "1791367058" "mode = 0;"
CHECKSUM: "2146436421 800920445"
INSTANCE: fixture_tb
Toggle 0to1 flag "logic flag"
Toggle 0to1 rst "logic rst"
Toggle 0to1 sel [0] "logic sel[1:0]"
Toggle 0to1 sel [1] "logic sel[1:0]"
Toggle 1to0 flag "logic flag"
Toggle 1to0 sel [0] "logic sel[1:0]"
Toggle 1to0 sel [1] "logic sel[1:0]"
CHECKSUM: "3483321271 1270723677"
INSTANCE: fixture_tb.left
Branch 0 "1916717874" "rst" (2) "rst 0,0,1,-"
Branch 0 "1916717874" "rst" (3) "rst 0,0,0,1"
Branch 0 "1916717874" "rst" (4) "rst 0,0,0,0"
CHECKSUM: "3483321271 1270723677"
INSTANCE: fixture_tb.right
Branch 0 "1916717874" "rst" (2) "rst 0,0,1,-"
Branch 0 "1916717874" "rst" (3) "rst 0,0,0,1"
Branch 0 "1916717874" "rst" (4) "rst 0,0,0,0"
CHECKSUM: "3483321271 1270723677"
INSTANCE: fixture_tb.wide
Branch 0 "1916717874" "rst" (2) "rst 0,0,1,-"
Branch 0 "1916717874" "rst" (3) "rst 0,0,0,1"
Branch 0 "1916717874" "rst" (4) "rst 0,0,0,0"
CHECKSUM: "3483321271 1850809875"
INSTANCE: fixture_tb.left
Condition 1 "3180802265" "(sel == 2'b0) 1 -1" (1 "0")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (1 "01")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (2 "10")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (3 "11")
Condition 3 "3879555943" "(sel == 2'b1) 1 -1" (1 "0")
Condition 3 "3879555943" "(sel == 2'b1) 1 -1" (2 "1")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (1 "00")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (2 "01")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (3 "10")
Condition 5 "1559485358" "(sel == 2'h2) 1 -1" (1 "0")
Condition 5 "1559485358" "(sel == 2'h2) 1 -1" (2 "1")
CHECKSUM: "3483321271 1850809875"
INSTANCE: fixture_tb.right
Condition 1 "3180802265" "(sel == 2'b0) 1 -1" (1 "0")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (1 "01")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (2 "10")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (3 "11")
Condition 3 "3879555943" "(sel == 2'b1) 1 -1" (1 "0")
Condition 3 "3879555943" "(sel == 2'b1) 1 -1" (2 "1")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (1 "00")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (2 "01")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (3 "10")
Condition 5 "1559485358" "(sel == 2'h2) 1 -1" (1 "0")
Condition 5 "1559485358" "(sel == 2'h2) 1 -1" (2 "1")
CHECKSUM: "3483321271 1850809875"
INSTANCE: fixture_tb.wide
Condition 1 "3180802265" "(sel == 2'b0) 1 -1" (1 "0")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (1 "01")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (2 "10")
Condition 2 "513732671" "((sel == 2'b1) && flag) 1 -1" (3 "11")
Condition 3 "3879555943" "(sel == 2'b1) 1 -1" (1 "0")
Condition 3 "3879555943" "(sel == 2'b1) 1 -1" (2 "1")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (1 "00")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (2 "01")
Condition 4 "847762555" "((sel == 2'h2) || flag) 1 -1" (3 "10")
Condition 5 "1559485358" "(sel == 2'h2) 1 -1" (1 "0")
Condition 5 "1559485358" "(sel == 2'h2) 1 -1" (2 "1")
CHECKSUM: "3483321271 2224335734"
INSTANCE: fixture_tb.wide
Toggle 0to1 flag "logic flag"
Toggle 0to1 rst "logic rst"
Toggle 0to1 seen "logic seen"
Toggle 0to1 sel [0] "logic sel[1:0]"
Toggle 0to1 sel [1] "logic sel[1:0]"
Toggle 1to0 flag "logic flag"
Toggle 1to0 seen "logic seen"
Toggle 1to0 sel [0] "logic sel[1:0]"
Toggle 1to0 sel [1] "logic sel[1:0]"
CHECKSUM: "3483321271 2358218364"
INSTANCE: fixture_tb.left
Toggle 0to1 flag "logic flag"
Toggle 0to1 rst "logic rst"
Toggle 0to1 seen "logic seen"
Toggle 0to1 sel [0] "logic sel[1:0]"
Toggle 0to1 sel [1] "logic sel[1:0]"
Toggle 1to0 flag "logic flag"
Toggle 1to0 seen "logic seen"
Toggle 1to0 sel [0] "logic sel[1:0]"
Toggle 1to0 sel [1] "logic sel[1:0]"
CHECKSUM: "3483321271 2358218364"
INSTANCE: fixture_tb.right
Toggle 0to1 flag "logic flag"
Toggle 0to1 rst "logic rst"
Toggle 0to1 seen "logic seen"
Toggle 0to1 sel [0] "logic sel[1:0]"
Toggle 0to1 sel [1] "logic sel[1:0]"
Toggle 1to0 flag "logic flag"
Toggle 1to0 seen "logic seen"
Toggle 1to0 sel [0] "logic sel[1:0]"
Toggle 1to0 sel [1] "logic sel[1:0]"
CHECKSUM: "3483321271 3088803612"
INSTANCE: fixture_tb.left
Block 5 "435466162" "if (((sel == 2'b1) && flag))"
Block 6 "4038202857" "value <= 1;"
Block 7 "3165114384" "if (((sel == 2'h2) || flag))"
Block 8 "3047523199" "value <= 2;"
Block 9 "1830688271" "value <= 3;"
CHECKSUM: "3483321271 3088803612"
INSTANCE: fixture_tb.right
Block 5 "435466162" "if (((sel == 2'b1) && flag))"
Block 6 "4038202857" "value <= 1;"
Block 7 "3165114384" "if (((sel == 2'h2) || flag))"
Block 8 "3047523199" "value <= 2;"
Block 9 "1830688271" "value <= 3;"
CHECKSUM: "3483321271 3088803612"
INSTANCE: fixture_tb.wide
Block 5 "435466162" "if (((sel == 2'b1) && flag))"
Block 6 "4038202857" "value <= 1;"
Block 7 "3165114384" "if (((sel == 2'h2) || flag))"
Block 8 "3047523199" "value <= 2;"
Block 9 "1830688271" "value <= 3;"
