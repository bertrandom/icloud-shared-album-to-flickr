// Dump the old Node app's LevelDB as a JSON array of [photoGuid, flickrPhotoId].
import { ClassicLevel } from 'classic-level';

const db = new ClassicLevel(process.argv[2], { createIfMissing: false });
const entries = [];
for await (const entry of db.iterator()) entries.push(entry);
await db.close();
console.log(JSON.stringify(entries));
