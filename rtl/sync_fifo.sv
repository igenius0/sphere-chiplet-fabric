`default_nettype none
// Single-clock, first-word-visible FIFO. Transfer only on valid && ready.
// DEPTH need not be a power of two; DEPTH=1 is supported. Reset is synchronous.
module sync_fifo #(
    parameter integer WIDTH = 32,
    parameter integer DEPTH = 4,
    parameter integer PTR_WIDTH = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter integer COUNT_WIDTH = $clog2(DEPTH + 1)
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire                   s_valid,
    output wire                   s_ready,
    input  wire [WIDTH-1:0]       s_data,
    output wire                   m_valid,
    input  wire                   m_ready,
    output wire [WIDTH-1:0]       m_data,
    output wire [COUNT_WIDTH-1:0] occupancy
);
    // Derived widths are part of the contract, not independent tuning knobs.
    // Constant guards disappear for supported synthesis configurations.
    initial begin
        if (WIDTH < 1 || DEPTH < 1 ||
            PTR_WIDTH != ((DEPTH <= 1) ? 1 : $clog2(DEPTH)) ||
            COUNT_WIDTH != $clog2(DEPTH + 1))
            $fatal(1, "sync_fifo parameter contract violated");
    end
    reg [WIDTH-1:0] storage [0:DEPTH-1];
    reg [PTR_WIDTH-1:0] read_pointer;
    reg [PTR_WIDTH-1:0] write_pointer;
    reg [COUNT_WIDTH-1:0] count;
    wire pop = m_valid && m_ready;
    wire push = s_valid && s_ready;

    assign m_valid = !rst && (count != 0);
    // A full FIFO accepts a replacement on the same edge that removes its head.
    assign s_ready = !rst && ((count < DEPTH) || pop);
    assign m_data = storage[read_pointer];
    assign occupancy = count;

    always @(posedge clk) begin
        if (rst) begin
            read_pointer <= 0;
            write_pointer <= 0;
            count <= 0;
        end else begin
            if (push) begin
                storage[write_pointer] <= s_data;
                if (write_pointer == DEPTH-1)
                    write_pointer <= 0;
                else
                    write_pointer <= write_pointer + 1'b1;
            end
            if (pop) begin
                if (read_pointer == DEPTH-1)
                    read_pointer <= 0;
                else
                    read_pointer <= read_pointer + 1'b1;
            end
            case ({push, pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: count <= count;
            endcase
        end
    end
endmodule
`default_nettype wire
