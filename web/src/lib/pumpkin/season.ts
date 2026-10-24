/** Halloween week: from 24 to 31 October (local time) the pumpkin is a carved jack-o'-lantern. From 1 November it
 * is the plain one again, by itself. */
export function isHalloween(now: Date = new Date()): boolean {
	return now.getMonth() === 9 && now.getDate() >= 24;
}
