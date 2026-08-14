export function garmentPath(family: 'tshirt' | 'shirt', sleeve: string, longSleeve: boolean): string {
  if (sleeve === 'sleeveless') return 'M205 82 L164 108 Q138 150 158 232 L176 710 L424 710 L442 232 Q462 150 436 108 L395 82 Q350 112 300 112 Q250 112 205 82 Z';
  if (sleeve === 'puff') return 'M205 82 L145 106 Q70 110 48 184 Q38 242 112 280 Q143 282 164 242 L176 710 L424 710 L436 242 Q457 282 488 280 Q562 242 552 184 Q530 110 455 106 L395 82 Q350 112 300 112 Q250 112 205 82 Z';
  if (sleeve === 'bell') return 'M205 82 L145 108 L64 176 L90 356 L150 342 L164 244 L176 710 L424 710 L436 244 L450 342 L510 356 L536 176 L455 108 L395 82 Q350 112 300 112 Q250 112 205 82 Z';
  if (sleeve === 'flutter') return 'M205 82 L142 105 L54 190 L108 318 L166 238 L176 710 L424 710 L434 238 L492 318 L546 190 L458 105 L395 82 Q350 112 300 112 Q250 112 205 82 Z';
  if (family === 'shirt' && longSleeve) return 'M205 82 L155 112 L72 172 L28 566 L104 582 L156 304 L174 720 L426 720 L444 304 L496 582 L572 566 L528 172 L445 112 L395 82 Q350 112 300 112 Q250 112 205 82 Z';
  if (sleeve === 'batwing') return 'M205 82 L118 118 L30 360 L135 390 L166 276 L176 710 L424 710 L434 276 L465 390 L570 360 L482 118 L395 82 Q350 112 300 112 Q250 112 205 82 Z';
  return 'M205 82 L130 116 L38 220 L124 292 L164 244 L176 710 L424 710 L436 244 L476 292 L562 220 L470 116 L395 82 Q350 112 300 112 Q250 112 205 82 Z';
}

export function specialDesignLines(special: string): Array<{ d: string; dashed?: boolean }> {
  if (special === 'side-waist-pleats') return [{ d: 'M176 390 L228 420 M176 430 L228 446 M424 390 L372 420 M424 430 L372 446' }];
  if (special === 'waist-gathers') return [{ d: 'M176 430 Q300 468 424 430', dashed: true }, { d: 'M210 420 L218 450 M250 424 L254 458 M350 424 L346 458 M390 420 L382 450' }];
  if (special === 'wrap-v') return [{ d: 'M205 105 Q250 250 405 410 M395 105 Q350 250 195 410' }];
  if (special === 'shoulder-pleats') return [{ d: 'M205 92 L232 170 M225 88 L252 166 M395 92 L368 170 M375 88 L348 166' }];
  return [];
}

export function necklinePath(neckline: string, view: 'front' | 'back'): string {
  if (view === 'back') return 'M252 82 Q300 118 348 82 L348 65 L252 65 Z';
  if (neckline.includes('v-neck') || neckline.includes('open-v')) return 'M248 78 L300 168 L352 78 L352 58 L248 58 Z';
  if (neckline.includes('boat')) return 'M220 78 Q300 112 380 78 L380 58 L220 58 Z';
  if (neckline.includes('high') || neckline.includes('mock')) return 'M270 82 Q300 96 330 82 L330 58 L270 58 Z';
  return 'M248 80 Q300 154 352 80 L352 58 L248 58 Z';
}
