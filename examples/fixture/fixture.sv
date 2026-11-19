// Controlled integration design, generated for this tool's regression suite.
// It supplements real-project VDBs and is not evidence for another production project.
module sample_leaf #(parameter WIDTH=2)(input logic clk, rst, input logic [1:0] sel,
    input logic flag, output logic [WIDTH-1:0] value);
    logic seen;
    always @(posedge clk) begin
        if (rst) begin value <= 0; seen <= 0; end
        else if (sel == 0) begin value <= value + 1; seen <= flag; end
        else if (sel == 1 && flag) begin value <= 1; seen <= 1; end
        else if (sel == 2 || flag) begin value <= 2; seen <= 0; end
        else begin value <= 3; seen <= !seen; end
    end
endmodule

module fixture_tb;
    logic clk=0, rst=1, flag=0;
    logic [1:0] sel=0;
    wire [1:0] a, b;
    wire [2:0] variant;
    integer mode, step;
    sample_leaf left(clk,rst,sel,flag,a);
    sample_leaf right(clk,rst,2'b00,flag,b);
    sample_leaf #(.WIDTH(3)) wide(clk,rst,sel,flag,variant);
    always #5 clk = !clk;
    initial begin
        if (!$value$plusargs("mode=%d",mode)) mode=0;
        #12 rst=0;
        for(step=0;step<12;step=step+1) begin
            @(negedge clk);
            sel = mode ? step[1:0] : 2'b00;
            flag = mode ? step[2] : 1'b0;
        end
        #12 $finish;
    end
endmodule
