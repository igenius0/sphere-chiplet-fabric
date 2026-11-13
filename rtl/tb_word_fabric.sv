`timescale 1ns/1ps
`default_nettype none
// Stimuli and independent expected cycles are produced by run_rtl_validation.py.
module tb_word_fabric;
    parameter integer PORTS = 4;
    parameter integer FIFO_DEPTH = 4;
    localparam integer PORT_WIDTH = (PORTS <= 1) ? 1 : $clog2(PORTS);
    reg clk = 0;
    always #5 clk = !clk;
    reg rst = 1;
    reg [PORTS-1:0] s_valid = 0;
    wire [PORTS-1:0] s_ready;
    reg [PORTS*32-1:0] s_payload = 0;
    reg [PORTS*PORT_WIDTH-1:0] s_destination = 0;
    reg [PORTS*16-1:0] s_sequence = 0;
    reg [PORTS*8-1:0] s_generation = 0;
    wire [PORTS-1:0] m_valid;
    reg [PORTS-1:0] m_ready = 0;
    wire [PORTS*32-1:0] m_payload;
    wire [PORTS*PORT_WIDTH-1:0] m_source;
    wire [PORTS*PORT_WIDTH-1:0] m_destination;
    wire [PORTS*16-1:0] m_sequence;
    wire [PORTS*8-1:0] m_generation;
    word_fabric_top #(.PORTS(PORTS), .FIFO_DEPTH(FIFO_DEPTH)) dut (.*);

    reg [PORTS-1:0] expected_ready;
    reg [PORTS-1:0] expected_valid;
    reg [PORTS*32-1:0] expected_payload;
    reg [PORTS*PORT_WIDTH-1:0] expected_source;
    reg [PORTS*PORT_WIDTH-1:0] expected_destination;
    reg [PORTS*16-1:0] expected_sequence;
    reg [PORTS*8-1:0] expected_generation;
    reg [PORTS-1:0] stalled = 0;
    reg [PORTS*32-1:0] previous_payload;
    reg [PORTS*PORT_WIDTH-1:0] previous_source;
    reg [PORTS*PORT_WIDTH-1:0] previous_destination;
    reg [PORTS*16-1:0] previous_sequence;
    reg [PORTS*8-1:0] previous_generation;
    reg [4095:0] stimulus_path;
    integer stimulus;
    integer scan_count;
    integer value;
    integer cycle = 0;
    integer endpoint;
    integer accepted = 0;
    integer delivered = 0;
    integer reset_flushed = 0;
    integer balance = 0;
    integer before_balance;
    integer stall_checks = 0;
    integer phase;
    integer fairness_age [0:PORTS-1];
    integer maximum_fairness = 0;
    integer hotspot_delivered = 0;

    task read_hex;
        output integer result;
        begin
            scan_count = $fscanf(stimulus, "%h", result);
            if (scan_count != 1) $fatal(1, "Malformed stimulus at cycle %0d", cycle);
        end
    endtask

    initial begin
        if (!$value$plusargs("STIMULUS=%s", stimulus_path))
            $fatal(1, "Missing +STIMULUS=path");
        stimulus = $fopen(stimulus_path, "r");
        if (!stimulus) $fatal(1, "Cannot open stimulus");
        for (endpoint = 0; endpoint < PORTS; endpoint = endpoint + 1)
            fairness_age[endpoint] = 0;
        while (!$feof(stimulus)) begin
            @(negedge clk);
            scan_count = $fscanf(stimulus, "%h", value);
            if (scan_count == 1) begin
                rst = value;
                read_hex(value); phase = value;
                read_hex(value); s_valid = value;
                read_hex(value); m_ready = value;
                read_hex(value); expected_ready = value;
                read_hex(value); expected_valid = value;
                for (endpoint = 0; endpoint < PORTS; endpoint = endpoint + 1) begin
                    read_hex(value); s_destination[endpoint*PORT_WIDTH +: PORT_WIDTH] = value;
                    read_hex(value); s_sequence[endpoint*16 +: 16] = value;
                    read_hex(value); s_generation[endpoint*8 +: 8] = value;
                    read_hex(value); s_payload[endpoint*32 +: 32] = value;
                end
                for (endpoint = 0; endpoint < PORTS; endpoint = endpoint + 1) begin
                    read_hex(value); expected_source[endpoint*PORT_WIDTH +: PORT_WIDTH] = value;
                    read_hex(value); expected_destination[endpoint*PORT_WIDTH +: PORT_WIDTH] = value;
                    read_hex(value); expected_sequence[endpoint*16 +: 16] = value;
                    read_hex(value); expected_generation[endpoint*8 +: 8] = value;
                    read_hex(value); expected_payload[endpoint*32 +: 32] = value;
                end
                #1;
                if (s_ready !== expected_ready || m_valid !== expected_valid ||
                    m_payload !== expected_payload || m_source !== expected_source ||
                    m_destination !== expected_destination || m_sequence !== expected_sequence ||
                    m_generation !== expected_generation)
                    $fatal(1, "Independent reference mismatch at cycle %0d: ready=%h/%h valid=%h/%h payload=%h/%h source=%h/%h dest=%h/%h seq=%h/%h gen=%h/%h",
                        cycle, s_ready, expected_ready, m_valid, expected_valid,
                        m_payload, expected_payload, m_source, expected_source,
                        m_destination, expected_destination, m_sequence, expected_sequence,
                        m_generation, expected_generation);
                if (rst) begin
                    reset_flushed = reset_flushed + balance;
                    balance = 0;
                    stalled = 0;
                    for (endpoint = 0; endpoint < PORTS; endpoint = endpoint + 1)
                        fairness_age[endpoint] = 0;
                end else begin
                    before_balance = balance;
                    for (endpoint = 0; endpoint < PORTS; endpoint = endpoint + 1) begin
                        if (stalled[endpoint]) begin
                            stall_checks = stall_checks + 1;
                            if (!m_valid[endpoint] ||
                                m_payload[endpoint*32 +: 32] !== previous_payload[endpoint*32 +: 32] ||
                                m_source[endpoint*PORT_WIDTH +: PORT_WIDTH] !== previous_source[endpoint*PORT_WIDTH +: PORT_WIDTH] ||
                                m_destination[endpoint*PORT_WIDTH +: PORT_WIDTH] !== previous_destination[endpoint*PORT_WIDTH +: PORT_WIDTH] ||
                                m_sequence[endpoint*16 +: 16] !== previous_sequence[endpoint*16 +: 16] ||
                                m_generation[endpoint*8 +: 8] !== previous_generation[endpoint*8 +: 8])
                                $fatal(1, "Output changed while stalled at cycle %0d output %0d", cycle, endpoint);
                        end
                        if (s_valid[endpoint] && s_ready[endpoint]) begin
                            accepted = accepted + 1;
                            balance = balance + 1;
                        end
                        if (m_valid[endpoint] && m_ready[endpoint]) begin
                            delivered = delivered + 1;
                            balance = balance - 1;
                        end
                    end
                    if (balance < 0 || balance > PORTS*FIFO_DEPTH)
                        $fatal(1, "Conservation violation at cycle %0d", cycle);
                    // Phase 2 is continuous all-input contention after priming.
                    if (phase == 2 && m_valid[0] && m_ready[0]) begin
                        hotspot_delivered = hotspot_delivered + 1;
                        for (endpoint = 0; endpoint < PORTS; endpoint = endpoint + 1) begin
                            if (m_source[0 +: PORT_WIDTH] == endpoint)
                                fairness_age[endpoint] = 0;
                            else
                                fairness_age[endpoint] = fairness_age[endpoint] + 1;
                            if (fairness_age[endpoint] > maximum_fairness)
                                maximum_fairness = fairness_age[endpoint];
                            if (fairness_age[endpoint] >= PORTS)
                                $fatal(1, "Round-robin starvation at cycle %0d input %0d", cycle, endpoint);
                        end
                    end else if (phase != 2) begin
                        for (endpoint = 0; endpoint < PORTS; endpoint = endpoint + 1)
                            fairness_age[endpoint] = 0;
                    end
                    stalled = m_valid & ~m_ready;
                end
                previous_payload = m_payload;
                previous_source = m_source;
                previous_destination = m_destination;
                previous_sequence = m_sequence;
                previous_generation = m_generation;
                @(posedge clk);
                #1;
                cycle = cycle + 1;
            end
        end
        if (balance != 0 || accepted != delivered + reset_flushed)
            $fatal(1, "Final conservation failure");
        if (hotspot_delivered < 1000 || stall_checks == 0 || reset_flushed == 0)
            $fatal(1, "Insufficient directed coverage");
        $display("FABRIC_PASS cycles=%0d accepted=%0d delivered=%0d reset_flushed=%0d stall_checks=%0d hotspot_delivered=%0d maximum_fairness=%0d",
            cycle, accepted, delivered, reset_flushed, stall_checks, hotspot_delivered, maximum_fairness);
        $fclose(stimulus);
        $finish;
    end
endmodule
`default_nettype wire
