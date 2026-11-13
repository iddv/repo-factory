def s_more(new, check):
    check("single_word", new().word_count("word").body.get("value") == 1)
