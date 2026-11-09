`timescale 1ns/1ps
`default_nettype none
module tb_sync_fifo;
    parameter integer DEPTH = 4;
    localparam integer COUNT_WIDTH = $clog2(DEPTH + 1);
    reg clk = 0;
    always #5 clk = !clk;
    reg rst = 1;
    reg s_valid = 0;
    wire s_ready;
    reg [31:0] s_data = 0;
    wire m_valid;
    reg m_ready = 0;
    wire [31:0] m_data;
    wire [COUNT_WIDTH-1:0] occupancy;
    sync_fifo #(.WIDTH(32), .DEPTH(DEPTH)) dut (.*);
    reg [31:0] reference [0:16383];
    integer read_index = 0;
    integer write_index = 0;
    integer expected_count = 0;
    integer cycle;
    integer pushes = 0;
    integer pops = 0;
    integer simultaneous_full = 0;
    integer resets_with_data = 0;
    reg [31:0] random_state = 32'h13579bdf;
    reg was_stalled = 0;
    reg [31:0] stalled_data;
    reg expected_ready;
    reg expected_valid;

    initial begin
        for (cycle = 0; cycle < 2048 + 4*DEPTH; cycle = cycle + 1) begin
            @(negedge clk);
            random_state = random_state ^ (random_state << 13);
            random_state = random_state ^ (random_state >> 17);
            random_state = random_state ^ (random_state << 5);
            rst = (cycle == 0 || cycle == 1 || cycle == 1024);
            // Fill, exercise a full replacement stream, then random stalls.
            s_valid = (cycle < 2 + 3*DEPTH) ? 1 : random_state[0];
            m_ready = (cycle < 2 + DEPTH) ? 0 : ((cycle < 2 + 3*DEPTH) ? 1 : random_state[1]);
            s_data = pushes + 32'h10000000;
            if (cycle >= 1024-DEPTH && cycle < 1024) begin
                s_valid = 1;
                m_ready = 0;
            end
            if (cycle >= 2000 + 4*DEPTH) begin
                rst = 0;
                s_valid = 0;
                m_ready = 1;
            end
            #1;
            expected_valid = !rst && expected_count != 0;
            expected_ready = !rst && ((expected_count < DEPTH) || (expected_valid && m_ready));
            if (m_valid !== expected_valid || s_ready !== expected_ready || occupancy !== expected_count)
                $fatal(1, "FIFO control mismatch depth=%0d cycle=%0d occupancy=%0d/%0d", DEPTH, cycle, occupancy, expected_count);
            if (m_valid && m_data !== reference[read_index])
                $fatal(1, "FIFO data/order mismatch depth=%0d cycle=%0d", DEPTH, cycle);
            if (!rst && was_stalled && (!m_valid || m_data !== stalled_data))
                $fatal(1, "FIFO changed stalled data");
            if (rst) begin
                if (expected_count != 0) resets_with_data = resets_with_data + 1;
                expected_count = 0;
                read_index = write_index;
                was_stalled = 0;
            end else begin
                if (expected_count == DEPTH && s_valid && s_ready && m_valid && m_ready)
                    simultaneous_full = simultaneous_full + 1;
                if (m_valid && m_ready) begin
                    read_index = read_index + 1;
                    expected_count = expected_count - 1;
                    pops = pops + 1;
                end
                if (s_valid && s_ready) begin
                    reference[write_index] = s_data;
                    write_index = write_index + 1;
                    expected_count = expected_count + 1;
                    pushes = pushes + 1;
                end
                was_stalled = m_valid && !m_ready;
                stalled_data = m_data;
            end
            @(posedge clk);
            #1;
        end
        if (expected_count != 0 || simultaneous_full == 0 || resets_with_data == 0)
            $fatal(1, "FIFO directed coverage missing: count=%0d full_replacements=%0d resets_with_data=%0d",
                expected_count, simultaneous_full, resets_with_data);
        $display("FIFO_PASS depth=%0d cycles=%0d pushes=%0d pops=%0d simultaneous_full=%0d resets_with_data=%0d",
            DEPTH, cycle, pushes, pops, simultaneous_full, resets_with_data);
        $finish;
    end
endmodule
`default_nettype wire
