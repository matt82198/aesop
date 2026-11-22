// Proof of a successful exit within three seconds during a held stream.
export function proofReceiptSchema(context) {
  const { data } = JSON.parse(context) ?? {};
  if (typeof data?.sha !== 'string' || !data.sha.trim() ||
      typeof data.shell !== 'string' || !data.shell.trim() || !Array.isArray(data.menu)) {
    throw new Error('expected data.sha, data.shell and data.menu');
  }

  const seen = new Set(['NONE']);
  const eligible = [];
  for (const row of data.menu) {
    if (typeof row?.id !== 'string' || !row.id.trim() || seen.has(row.id)) {
      throw new Error('receipt IDs must be unique nonempty strings distinct from NONE');
    }
    seen.add(row.id);
    if (row.sha === data.sha && row.shell === data.shell && row.scenario === 'exit during held stream' &&
        row.exit === 0 && Number.isFinite(row.seconds) && row.seconds >= 0 && row.seconds <= 3 &&
        (row.ok === undefined || row.ok === true) && (row.timed_out === undefined || row.timed_out === false)) {
      eligible.push(row.id);
    }
  }
  return { type: 'string', enum: [...eligible, 'NONE'] };
}

export function acceptsProofReceipt(schema, text) {
  try {
    return schema.enum.includes(JSON.parse(text));
  } catch {
    return false;
  }
}
