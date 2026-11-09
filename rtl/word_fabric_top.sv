`default_nettype none
// Board-agnostic word switch, not a routed chiplet network or a physical PHY.
// Each ingress has one FIFO. Head-of-line blocking is intentional and visible.
module word_fabric_top #(
    parameter integer PORTS = 4,
    parameter integer FIFO_DEPTH = 4,
    parameter integer PAYLOAD_WIDTH = 32,
    parameter integer SEQUENCE_WIDTH = 16,
    parameter integer GENERATION_WIDTH = 8,
    parameter integer PORT_WIDTH = (PORTS <= 1) ? 1 : $clog2(PORTS)
) (
    input wire clk,
    input wire rst,
    input wire [PORTS-1:0] s_valid,
    output wire [PORTS-1:0] s_ready,
    input wire [PORTS*PAYLOAD_WIDTH-1:0] s_payload,
    input wire [PORTS*PORT_WIDTH-1:0] s_destination,
    input wire [PORTS*SEQUENCE_WIDTH-1:0] s_sequence,
    input wire [PORTS*GENERATION_WIDTH-1:0] s_generation,
    output reg [PORTS-1:0] m_valid,
    input wire [PORTS-1:0] m_ready,
    output reg [PORTS*PAYLOAD_WIDTH-1:0] m_payload,
    output reg [PORTS*PORT_WIDTH-1:0] m_source,
    output reg [PORTS*PORT_WIDTH-1:0] m_destination,
    output reg [PORTS*SEQUENCE_WIDTH-1:0] m_sequence,
    output reg [PORTS*GENERATION_WIDTH-1:0] m_generation
);
    initial begin
        if (PORTS < 2 || FIFO_DEPTH < 1 || PAYLOAD_WIDTH < 1 ||
            SEQUENCE_WIDTH < 1 || GENERATION_WIDTH < 1 ||
            PORT_WIDTH != ((PORTS <= 1) ? 1 : $clog2(PORTS)))
            $fatal(1, "word_fabric_top parameter contract violated");
    end
    localparam integer SOURCE_LOW = PAYLOAD_WIDTH;
    localparam integer DEST_LOW = SOURCE_LOW + PORT_WIDTH;
    localparam integer SEQ_LOW = DEST_LOW + PORT_WIDTH;
    localparam integer GEN_LOW = SEQ_LOW + SEQUENCE_WIDTH;
    localparam integer WORD_WIDTH = GEN_LOW + GENERATION_WIDTH;
    wire [WORD_WIDTH-1:0] head [0:PORTS-1];
    wire [PORTS-1:0] head_valid;
    wire [PORTS-1:0] fifo_ready;
    reg [PORTS-1:0] pop;
    reg [PORT_WIDTH-1:0] round_robin [0:PORTS-1];
    reg [PORTS-1:0] locked;
    reg [PORT_WIDTH-1:0] locked_source [0:PORTS-1];
    reg [PORT_WIDTH-1:0] grant [0:PORTS-1];
    reg [PORTS-1:0] grant_valid;
    integer output_port;
    integer offset;
    integer candidate;
    integer state_port;

    genvar ingress;
    generate for (ingress = 0; ingress < PORTS; ingress = ingress + 1) begin: input_queue
        localparam [PORT_WIDTH-1:0] SOURCE_ID = ingress;
        wire [PORT_WIDTH-1:0] destination = s_destination[ingress*PORT_WIDTH +: PORT_WIDTH];
        wire legal_destination = (destination < PORTS);
        wire [WORD_WIDTH-1:0] incoming_word = {
            s_generation[ingress*GENERATION_WIDTH +: GENERATION_WIDTH],
            s_sequence[ingress*SEQUENCE_WIDTH +: SEQUENCE_WIDTH],
            destination, SOURCE_ID,
            s_payload[ingress*PAYLOAD_WIDTH +: PAYLOAD_WIDTH]
        };
        // Unused destination encodings are never accepted.
        assign s_ready[ingress] = legal_destination && fifo_ready[ingress];
        sync_fifo #(.WIDTH(WORD_WIDTH), .DEPTH(FIFO_DEPTH)) queue (
            .clk(clk), .rst(rst),
            .s_valid(s_valid[ingress] && legal_destination),
            .s_ready(fifo_ready[ingress]), .s_data(incoming_word),
            .m_valid(head_valid[ingress]), .m_ready(pop[ingress]),
            .m_data(head[ingress]), .occupancy()
        );
    end endgenerate

    // Grants depend only on registered FIFO heads and arbitration state.
    // Ready only qualifies a transfer; it cannot create a combinational cycle.
    always @* begin
        pop = 0;
        m_valid = 0;
        m_payload = 0;
        m_source = 0;
        m_destination = 0;
        m_sequence = 0;
        m_generation = 0;
        grant_valid = 0;
        candidate = 0;
        for (output_port = 0; output_port < PORTS; output_port = output_port + 1) begin
            grant[output_port] = 0;
            if (locked[output_port]) begin
                grant[output_port] = locked_source[output_port];
                grant_valid[output_port] = head_valid[locked_source[output_port]];
            end else begin
                for (offset = 0; offset < PORTS; offset = offset + 1) begin
                    candidate = round_robin[output_port] + offset;
                    if (candidate >= PORTS)
                        candidate = candidate - PORTS;
                    if (!grant_valid[output_port] && head_valid[candidate] &&
                        (head[candidate][DEST_LOW +: PORT_WIDTH] == output_port)) begin
                        grant[output_port] = candidate;
                        grant_valid[output_port] = 1'b1;
                    end
                end
            end
            if (!rst && grant_valid[output_port]) begin
                m_valid[output_port] = 1'b1;
                m_payload[output_port*PAYLOAD_WIDTH +: PAYLOAD_WIDTH] = head[grant[output_port]][0 +: PAYLOAD_WIDTH];
                m_source[output_port*PORT_WIDTH +: PORT_WIDTH] = head[grant[output_port]][SOURCE_LOW +: PORT_WIDTH];
                m_destination[output_port*PORT_WIDTH +: PORT_WIDTH] = head[grant[output_port]][DEST_LOW +: PORT_WIDTH];
                m_sequence[output_port*SEQUENCE_WIDTH +: SEQUENCE_WIDTH] = head[grant[output_port]][SEQ_LOW +: SEQUENCE_WIDTH];
                m_generation[output_port*GENERATION_WIDTH +: GENERATION_WIDTH] = head[grant[output_port]][GEN_LOW +: GENERATION_WIDTH];
                if (m_ready[output_port])
                    pop[grant[output_port]] = 1'b1;
            end
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            locked <= 0;
            for (state_port = 0; state_port < PORTS; state_port = state_port + 1) begin
                round_robin[state_port] <= 0;
                locked_source[state_port] <= 0;
            end
        end else begin
            for (state_port = 0; state_port < PORTS; state_port = state_port + 1) begin
                if (grant_valid[state_port]) begin
                    if (m_ready[state_port]) begin
                        locked[state_port] <= 1'b0;
                        if (grant[state_port] == PORTS-1)
                            round_robin[state_port] <= 0;
                        else
                            round_robin[state_port] <= grant[state_port] + 1'b1;
                    end else begin
                        // A stalled word retains its owner even if new inputs arrive.
                        locked[state_port] <= 1'b1;
                        locked_source[state_port] <= grant[state_port];
                    end
                end
            end
        end
    end
endmodule
`default_nettype wire
