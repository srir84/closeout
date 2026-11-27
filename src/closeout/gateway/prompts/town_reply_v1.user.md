Fields:
- permit_number: the permit number the letter is about, exactly as printed (for example 22-0147)
- result: final_on_file if the letter says a final approval, certificate of approval or passed final inspection is on file for that permit; no_record if it says no record of a final approval or final inspection was found; unclear for anything else (scheduled, partial, missing details, a different permit, or a request for more information)
- result_date: the date of the final approval as YYYY-MM-DD when result is final_on_file, otherwise an empty string