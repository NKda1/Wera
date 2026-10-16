import "server-only";

// The shared page: one column, standard headings, no tables, images or text boxes (ATSC-05).
// Both the CV and the letter are the same Block list on the same page, which is why one
// lint pass covers all of them.

import { Document, type DocumentProps, Page, StyleSheet, Text, View } from "@react-pdf/renderer";
import type { ReactElement } from "react";
import type { Block, ExportDoc } from "./content";
import { FONT_FAMILY, registerFonts } from "./fonts";

const styles = StyleSheet.create({
  page: {
    fontFamily: FONT_FAMILY,
    fontSize: 10.5,
    lineHeight: 1.4,
    color: "#1A1A1A",
    paddingTop: 46,
    paddingBottom: 46,
    paddingHorizontal: 50,
  },
  name: { fontSize: 20, fontWeight: 700, marginBottom: 4 },
  header: { fontSize: 9.5, color: "#444444" },
  rule: { borderBottomWidth: 1.2, borderBottomColor: "#1A1A1A", marginTop: 8, marginBottom: 12 },
  // No letter spacing: it makes each glyph its own text run, and extraction then reads
  // "S U M M A R Y" (ATSC-06).
  heading: { fontSize: 10, fontWeight: 700, marginTop: 12, marginBottom: 5 },
  row: { flexDirection: "row", justifyContent: "space-between", marginTop: 6 },
  rowText: { fontWeight: 700, flexShrink: 1, paddingRight: 12 },
  rowRight: { color: "#444444" },
  bullet: { flexDirection: "row", marginTop: 2.5 },
  bulletMark: { width: 10 },
  body: { marginTop: 6 },
});

function Blocks({ blocks }: { blocks: Block[] }) {
  return blocks.map((block, i) => {
    switch (block.t) {
      case "heading":
        // Upper-cased in the string itself, so the extracted text matches what lint expects.
        return (
          <Text key={i} style={styles.heading}>
            {block.text.toUpperCase()}
          </Text>
        );
      case "row":
        return (
          <View key={i} style={styles.row} wrap={false}>
            <Text style={styles.rowText}>{block.text}</Text>
            {block.right ? <Text style={styles.rowRight}>{block.right}</Text> : null}
          </View>
        );
      case "bullet":
        return (
          <View key={i} style={styles.bullet}>
            <Text style={styles.bulletMark}>•</Text>
            <Text style={{ flex: 1 }}>{block.text}</Text>
          </View>
        );
      case "body":
        return (
          <Text key={i} style={styles.body}>
            {block.text}
          </Text>
        );
    }
  });
}

/** The element `renderToBuffer` takes; typed as a Document so react-pdf accepts it. */
export function paper(doc: ExportDoc): ReactElement<DocumentProps> {
  registerFonts();
  return (
    <Document
      title={doc.title}
      author={doc.name}
      subject={doc.kind === "cv" ? "Curriculum vitae" : "Cover letter"}
      creator="WERA"
      producer="WERA"
      language="en-GB"
      // EXP-03: fixed dates, so the same pack renders byte-identical files.
      creationDate={doc.date}
      modificationDate={doc.date}
    >
      <Page size="A4" style={styles.page}>
        <Text style={styles.name}>{doc.name}</Text>
        {doc.header.map((line, i) => (
          <Text key={i} style={styles.header}>
            {line}
          </Text>
        ))}
        <View style={styles.rule} />
        <Blocks blocks={doc.blocks} />
      </Page>
    </Document>
  );
}
