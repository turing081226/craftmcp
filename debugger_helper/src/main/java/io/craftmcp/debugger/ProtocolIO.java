package io.craftmcp.debugger;

import com.google.gson.JsonElement;
import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;

final class ProtocolIO {
    static final int MAX_LINE_BYTES = 1024 * 1024;
    private final InputStream input;
    private final OutputStream output;

    ProtocolIO(InputStream input, OutputStream output) {
        this.input = new BufferedInputStream(input);
        this.output = output;
    }

    String readLine() throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        int next;
        while ((next = input.read()) != -1 && next != '\n') {
            if (bytes.size() >= MAX_LINE_BYTES) throw new IOException("Input line exceeds 1 MiB.");
            bytes.write(next);
        }
        if (next == -1 && bytes.size() == 0) return null;
        return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes.toByteArray())).toString();
    }

    synchronized void write(JsonElement value) {
        byte[] bytes = Json.GSON.toJson(value).getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_LINE_BYTES) throw new IllegalStateException("Output line exceeds 1 MiB.");
        try {
            output.write(bytes);
            output.write('\n');
            output.flush();
        } catch (IOException exception) { throw new UncheckedIOException(exception); }
    }
}
