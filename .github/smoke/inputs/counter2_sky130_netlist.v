// Synthetic 2-bit counter, gate-level SKY130 HD netlist for the opensta smoke test.
// Hand-written from standard cells in sky130hd_tt.lib (dfxtp_1, inv_1, xor2_1).
module counter2 (clk, q);
  input clk;
  output [1:0] q;
  wire n0;
  wire n1;

  sky130_fd_sc_hd__inv_1 u_inv0 (.A(q[0]), .Y(n0));
  sky130_fd_sc_hd__xor2_1 u_xor1 (.A(q[1]), .B(q[0]), .X(n1));
  sky130_fd_sc_hd__dfxtp_1 u_ff0 (.CLK(clk), .D(n0), .Q(q[0]));
  sky130_fd_sc_hd__dfxtp_1 u_ff1 (.CLK(clk), .D(n1), .Q(q[1]));
endmodule
