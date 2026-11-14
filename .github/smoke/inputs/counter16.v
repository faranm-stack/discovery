// 16-bit synchronous up-counter with active-high synchronous reset and enable.
module counter16 (
    input  wire        clk,
    input  wire        rst,
    input  wire        en,
    output reg  [15:0] count
);
    always @(posedge clk) begin
        if (rst)
            count <= 16'd0;
        else if (en)
            count <= count + 16'd1;
    end
endmodule
