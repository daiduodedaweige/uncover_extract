module leaf #(parameter W=4)(input logic clk, rst, en,
    input logic [3:0] d, input logic signed [1:0] sd,
    output logic [W-1:0] q, output wire [5:0] ext, cat,
    output logic c);
    assign ext = sd;
    assign cat = {d[1:0], d[3:0]};
    always_ff @(posedge clk)
        if (rst) q <= 0;
        else if (en) q <= d;
    always_comb begin
        casez (d)
            4'b1???: c = 1;
            default: c = 0;
        endcase
    end
endmodule

module top(input logic clk, rst, en, input logic [3:0] d,
    input logic signed [1:0] sd, output wire [3:0] q);
    wire [5:0] ext, cat;
    wire c;
    leaf u(clk, rst, en, d, sd, q, ext, cat, c);
    for (genvar j=0; j<2; j++) begin: g
        wire [j+1:0] v;
        leaf #(.W(j+2)) vinst(clk, rst, en, d, sd, v, , , );
    end
endmodule
