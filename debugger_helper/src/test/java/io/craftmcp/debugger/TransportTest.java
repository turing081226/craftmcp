package io.craftmcp.debugger;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Set;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class TransportTest {
    @Test void utf8AndEmbeddedNewlineRoundTrip() throws Exception {
        var bytes = new ByteArrayOutputStream();
        var io = new ProtocolIO(new ByteArrayInputStream(new byte[0]), bytes);
        io.write(Json.object("text", "테스트\n두 번째 줄"));
        String wire = bytes.toString(StandardCharsets.UTF_8);
        assertEquals(1, wire.chars().filter(value -> value == '\n').count());
        var decoded = Json.GSON.fromJson(wire, com.google.gson.JsonObject.class);
        assertEquals("테스트\n두 번째 줄", decoded.get("text").getAsString());
    }

    @Test void boundsInputAndRejectsInvalidUtf8() {
        var huge = new byte[ProtocolIO.MAX_LINE_BYTES + 1];
        java.util.Arrays.fill(huge, (byte) 'a');
        assertThrows(IOException.class, () -> new ProtocolIO(new ByteArrayInputStream(huge), new ByteArrayOutputStream()).readLine());
        assertThrows(IOException.class, () -> new ProtocolIO(new ByteArrayInputStream(new byte[]{(byte) 0xff}), new ByteArrayOutputStream()).readLine());
    }

    @Test void rejectsUnknownParametersAndNumericStrings() {
        assertThrows(Fault.class, () -> new Params(Json.object("unexpected", true), Set.of(), Set.of()));
        Params p = new Params(Json.object("limit", "10"), Set.of("limit"), Set.of());
        assertEquals("invalid_params", assertThrows(Fault.class, () -> p.integer("limit", 1, 100)).code);
        Params fraction = new Params(Json.object("limit", 1.5), Set.of("limit"), Set.of());
        assertThrows(Fault.class, () -> fraction.integer("limit", 1, 100));
    }
}
