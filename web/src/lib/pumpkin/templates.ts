/** Ready-made tasks. Words in <angle brackets> are for the person to fill in before saving. */
export interface TaskTemplate {
	days: 'daily' | 'weekdays' | 'weekends';
	name: string;
	prompt: string;
	summary: string;
	time: string;
}

export const TASK_TEMPLATES: TaskTemplate[] = [
	{
		days: 'weekdays',
		name: 'Morning news briefing',
		summary: 'The five stories that matter today, with sources.',
		time: '08:00',
		prompt:
			"Search today's top world news. Pick the 5 most important stories, open one article for each, and write a short briefing with the source URL under each item."
	},
	{
		days: 'weekdays',
		name: 'Security news',
		summary: 'New critical vulnerabilities and incidents from the last day.',
		time: '09:00',
		prompt:
			'Search for critical security vulnerabilities and major incidents published in the last 24 hours. Open the vendor advisory or a reliable news source for each of the top 5. For each one give the product, what is affected, whether a fix exists, and the source URL.'
	},
	{
		days: 'weekdays',
		name: 'Weather and what to wear',
		summary: 'Today and tomorrow for your city.',
		time: '07:30',
		prompt:
			'Look up the weather forecast for <your city> today and tomorrow. Tell me in three sentences what it will be like and what to wear. Include the source URL.'
	},
	{
		days: 'daily',
		name: 'Learn one thing',
		summary: 'A five-minute lesson on a topic you choose, with a quiz question.',
		time: '20:00',
		prompt:
			'Teach me one new thing about <topic> that I probably do not know yet. Keep it to five minutes of reading, give one concrete example, and finish with one question to check I understood.'
	},
	{
		days: 'weekdays',
		name: 'Price watch',
		summary: 'Checks what something costs today.',
		time: '10:00',
		prompt:
			'Search for the current price of <product> from at least three shops. Give me a table of shop, price and URL, and say which one is cheapest right now.'
	},
	{
		days: 'weekends',
		name: 'Weekly tech digest',
		summary: 'What happened in AI and open source this week.',
		time: '10:00',
		prompt:
			'Search for the most important AI and open-source software news from the past week. Pick the 6 most interesting items, open each source, and write a digest with two sentences and the URL for each.'
	}
];
